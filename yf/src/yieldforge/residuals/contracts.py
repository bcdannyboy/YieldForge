"""Strict persisted contracts for exact residual geometry evidence."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from yieldforge.experiments.contracts import RemnantEligibility, RemnantRule

Sha256 = StrictStr
ForbiddenRing = tuple[tuple[StrictFloat, StrictFloat], ...]
_RULE_ORDER: dict[str, int] = {"permissive": 0, "primary": 1, "conservative": 2}


class ResidualContractModel(BaseModel):
    """Immutable, finite, strict base for residual evidence."""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
    )


class ResidualRuleName(StrEnum):
    """Stable reporting names for the three frozen M0 rules."""

    PERMISSIVE = "permissive"
    PRIMARY = "primary"
    CONSERVATIVE = "conservative"


class ResidualGeometryConfig(ResidualContractModel):
    """Explicit geometry inputs that may change residual material."""

    schema_version: Literal["yieldforge.residual-geometry-config.v1"] = (
        "yieldforge.residual-geometry-config.v1"
    )
    process_model: Literal["explicit_symmetric_part_buffer"] = (
        "explicit_symmetric_part_buffer"
    )
    part_buffer_distance: StrictFloat = Field(default=0.0, ge=0)
    forbidden_polygons: tuple[ForbiddenRing, ...] = ()
    coordinate_tolerance: StrictFloat = Field(default=1e-7, gt=0)
    relative_area_tolerance: StrictFloat = Field(default=1e-10, gt=0)

    @field_validator("forbidden_polygons")
    @classmethod
    def require_closed_nonempty_rings(
        cls, value: tuple[ForbiddenRing, ...]
    ) -> tuple[ForbiddenRing, ...]:
        for ring in value:
            if len(ring) < 4 or ring[0] != ring[-1]:
                raise ValueError("forbidden polygon rings must be closed")
            if len(set(ring[:-1])) < 3:
                raise ValueError("forbidden polygon rings require three distinct points")
        return value


class ResidualRule(ResidualContractModel):
    """One geometry-only projection of an approved M0 remnant rule."""

    rule_name: ResidualRuleName
    source_name: StrictStr = Field(min_length=1)
    minimum_area_sheet_fraction: StrictFloat = Field(ge=0, le=1)
    minimum_effective_width_short_side_fraction: StrictFloat = Field(ge=0, le=1)
    minimum_exterior_access_short_side_fraction: StrictFloat = Field(ge=0, le=1)
    effective_width_test: Literal["nonempty_inward_buffer_by_half_minimum_width"]
    requires_exterior_connection: StrictBool


class ResidualRuleSet(ResidualContractModel):
    """The complete ordered M0 remnant sensitivity grid."""

    permissive: ResidualRule
    primary: ResidualRule
    conservative: ResidualRule

    @model_validator(mode="after")
    def require_matching_names(self) -> Self:
        expected = (
            (self.permissive, ResidualRuleName.PERMISSIVE),
            (self.primary, ResidualRuleName.PRIMARY),
            (self.conservative, ResidualRuleName.CONSERVATIVE),
        )
        if any(rule.rule_name is not name for rule, name in expected):
            raise ValueError("residual rule fields must match their stable names")
        return self

    def ordered(self) -> tuple[ResidualRule, ResidualRule, ResidualRule]:
        """Return rules in the predeclared sensitivity order."""

        return (self.permissive, self.primary, self.conservative)


def _residual_rule(name: ResidualRuleName, source: RemnantRule) -> ResidualRule:
    return ResidualRule(
        rule_name=name,
        source_name=source.name,
        minimum_area_sheet_fraction=source.minimum_area_sheet_fraction,
        minimum_effective_width_short_side_fraction=(
            source.minimum_effective_width_short_side_fraction
        ),
        minimum_exterior_access_short_side_fraction=(
            source.minimum_exterior_access_short_side_fraction
        ),
        effective_width_test=source.effective_width_test,
        requires_exterior_connection=source.requires_exterior_connection,
    )


def rule_set_from_m0(eligibility: RemnantEligibility) -> ResidualRuleSet:
    """Project the approved M0 eligibility grid into the geometry boundary."""

    return ResidualRuleSet(
        permissive=_residual_rule(
            ResidualRuleName.PERMISSIVE, eligibility.permissive_sensitivity
        ),
        primary=_residual_rule(ResidualRuleName.PRIMARY, eligibility.primary),
        conservative=_residual_rule(
            ResidualRuleName.CONSERVATIVE, eligibility.conservative_sensitivity
        ),
    )


class ResidualComponentMetrics(ResidualContractModel):
    """Persisted exact identity and eligibility diagnostics for one component."""

    component_sha256: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    area: StrictFloat = Field(ge=0)
    bounds: tuple[StrictFloat, StrictFloat, StrictFloat, StrictFloat]
    hole_count: StrictInt = Field(ge=0)
    exterior_connected: StrictBool
    exterior_access_length: StrictFloat = Field(ge=0)
    effective_width_rule_names: tuple[ResidualRuleName, ...] = ()

    @field_validator("effective_width_rule_names")
    @classmethod
    def require_ordered_unique_rules(
        cls, value: tuple[ResidualRuleName, ...]
    ) -> tuple[ResidualRuleName, ...]:
        if len(value) != len(set(value)):
            raise ValueError("effective width rule names must be unique")
        if tuple(sorted(value, key=lambda item: _RULE_ORDER[item.value])) != value:
            raise ValueError("effective width rule names must use registered order")
        return value


class RuleClassificationSummary(ResidualContractModel):
    """Retained-versus-scrap accounting under one frozen M0 rule."""

    rule_name: ResidualRuleName
    retained_component_sha256: tuple[Sha256, ...]
    scrap_component_sha256: tuple[Sha256, ...]
    retained_area: StrictFloat = Field(ge=0)
    scrap_area: StrictFloat = Field(ge=0)
    largest_retained_component_area: StrictFloat = Field(ge=0)

    @field_validator("retained_component_sha256", "scrap_component_sha256")
    @classmethod
    def require_sorted_unique_hashes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("component hashes must be sorted and unique")
        return value

    @model_validator(mode="after")
    def require_disjoint_components(self) -> Self:
        if set(self.retained_component_sha256) & set(self.scrap_component_sha256):
            raise ValueError("retained and scrap components must be disjoint")
        return self


class ResidualAccounting(ResidualContractModel):
    """Disjoint fixed-sheet material categories for one candidate."""

    stock_area: StrictFloat = Field(gt=0)
    placed_area: StrictFloat = Field(ge=0)
    process_loss_area: StrictFloat = Field(ge=0)
    forbidden_loss_area: StrictFloat = Field(ge=0)
    residual_area: StrictFloat = Field(ge=0)
    reconciliation_delta: StrictFloat = Field(ge=0)
    area_tolerance: StrictFloat = Field(gt=0)


class CandidateResidualObservation(ResidualContractModel):
    """One valid residual state or one fail-closed error."""

    candidate_id: StrictStr = Field(min_length=1)
    valid: StrictBool
    error_code: StrictStr | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")
    residual_sha256: Sha256 | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    accounting: ResidualAccounting | None = None
    components: tuple[ResidualComponentMetrics, ...] = ()
    classifications: tuple[RuleClassificationSummary, ...] = ()

    @model_validator(mode="after")
    def require_consistent_validity(self) -> Self:
        if self.valid:
            if self.error_code is not None:
                raise ValueError("valid observation cannot carry an error code")
            if self.residual_sha256 is None or self.accounting is None:
                raise ValueError("valid observation requires geometry and accounting evidence")
        elif (
            self.error_code is None
            or self.residual_sha256 is not None
            or self.accounting is not None
            or self.components
            or self.classifications
        ):
            raise ValueError("invalid observation may carry only an error code")

        component_hashes = tuple(item.component_sha256 for item in self.components)
        if component_hashes != tuple(sorted(set(component_hashes))):
            raise ValueError("observation components must be sorted and unique")
        classification_names = tuple(item.rule_name for item in self.classifications)
        if len(classification_names) != len(set(classification_names)):
            raise ValueError("observation classifications must be unique")
        if tuple(
            sorted(classification_names, key=lambda item: _RULE_ORDER[item.value])
        ) != classification_names:
            raise ValueError("observation classifications must use registered order")
        return self


class ResidualPairComparison(ResidualContractModel):
    """Exact and tolerance-aware diagnostics for one candidate pair."""

    first_candidate_id: StrictStr = Field(min_length=1)
    second_candidate_id: StrictStr = Field(min_length=1)
    exact_residual_equal: StrictBool
    symmetric_difference_area: StrictFloat = Field(ge=0)
    symmetric_difference_sheet_fraction: StrictFloat = Field(ge=0, le=1)
    classification_difference_rule_names: tuple[ResidualRuleName, ...] = ()

    @field_validator("classification_difference_rule_names")
    @classmethod
    def require_ordered_unique_rules(
        cls, value: tuple[ResidualRuleName, ...]
    ) -> tuple[ResidualRuleName, ...]:
        if len(value) != len(set(value)):
            raise ValueError("classification difference rules must be unique")
        if tuple(sorted(value, key=lambda item: _RULE_ORDER[item.value])) != value:
            raise ValueError("classification difference rules must use registered order")
        return value


class ResidualGeometryError(ValueError):
    """A stable fail-closed residual geometry error."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)
