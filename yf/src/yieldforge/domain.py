"""Canonical contracts used at the solver boundary."""

from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

Point = tuple[float, float]


class ContractModel(BaseModel):
    """Strict, immutable base model for persisted experiment contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Part(ContractModel):
    """A repeated simple polygon accepted by the strip-packing solver."""

    id: str = Field(min_length=1)
    shape: list[Point] = Field(min_length=3)
    demand: int = Field(gt=0)
    allowed_orientations: list[float] | None = Field(default_factory=lambda: [0.0])

    @field_validator("shape")
    @classmethod
    def close_polygon(cls, value: list[Point]) -> list[Point]:
        points = list(value)
        if points[0] != points[-1]:
            points.append(points[0])
        if len(set(points[:-1])) < 3:
            raise ValueError("shape must contain at least three distinct points")
        return points


class StripPackingProblem(ContractModel):
    """A fixed-height strip problem bounded by a physical sheet length."""

    name: str = Field(min_length=1)
    strip_height: float = Field(gt=0)
    sheet_length: float = Field(gt=0)
    parts: list[Part] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_part_ids(self) -> Self:
        ids = [part.id for part in self.parts]
        if len(ids) != len(set(ids)):
            raise ValueError("part IDs must be unique")
        return self


class SpyrrowRunConfig(ContractModel):
    """The solver settings that define one reproducible candidate run."""

    seed: int
    total_computation_time: int = Field(default=10, gt=0)
    early_termination: bool = False
    num_workers: int = Field(default=1, gt=0)
    min_items_separation: float | None = Field(default=None, ge=0)


class Placement(ContractModel):
    """A solver-independent rigid transform for one part instance."""

    part_id: str = Field(min_length=1)
    rotation: float
    translation: Point


class CandidateReportType(StrEnum):
    """Spyrrow progress states that are safe to archive as feasible candidates."""

    EXPLORATION_FEASIBLE = "exploration_feasible"
    COMPRESSION_FEASIBLE = "compression_feasible"
    FINAL = "final"


class SolverIdentity(ContractModel):
    """Version identity persisted with every candidate batch."""

    name: Literal["spyrrow"] = "spyrrow"
    spyrrow_version: str = Field(min_length=1)
    sparrow_revision: str | None = None


class Candidate(ContractModel):
    """One normalized, feasible strip-packing layout."""

    candidate_id: str = Field(min_length=1)
    report_type: CandidateReportType
    seed: int
    width: float = Field(gt=0)
    density: float = Field(ge=0, le=1)
    placements: list[Placement] = Field(min_length=1)


class CandidateBatch(ContractModel):
    """The complete result of one configured adapter invocation."""

    schema_version: Literal["yieldforge.candidates.v1"] = "yieldforge.candidates.v1"
    problem: StripPackingProblem
    solver: SolverIdentity
    config: SpyrrowRunConfig
    candidates: list[Candidate]


class SpyrrowRunResult(ContractModel):
    """A completed adapter run with truthful native-report accounting."""

    schema_version: Literal["yieldforge.spyrrow-run.v1"] = "yieldforge.spyrrow-run.v1"
    batch: CandidateBatch
    final_candidate_id: str | None = Field(default=None, min_length=1)
    native_report_count: StrictInt = Field(ge=0)
    terminal_observation_count: StrictInt = Field(default=1, ge=1, le=1)
    ignored_report_count: StrictInt = Field(ge=0)
    duplicate_candidate_count: StrictInt = Field(ge=0)
    sheet_overflow_count: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def require_consistent_report_accounting(self) -> Self:
        candidate_ids = [candidate.candidate_id for candidate in self.batch.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidate IDs must be unique")
        if self.final_candidate_id is not None and self.final_candidate_id not in candidate_ids:
            raise ValueError("final candidate ID must reference a batch candidate")

        observations = self.native_report_count + self.terminal_observation_count
        classified = (
            len(candidate_ids)
            + self.ignored_report_count
            + self.duplicate_candidate_count
            + self.sheet_overflow_count
        )
        if observations != classified:
            raise ValueError(
                f"report accounting mismatch: {observations} observations != "
                f"{classified} classifications"
            )
        return self
