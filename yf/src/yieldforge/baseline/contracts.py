"""Strict content-addressed contracts for the M7 strong baseline."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from yieldforge.domain import SolverProjectionBinding, StripPackingProblem
from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.reuse.contracts import MaterialIdentity, MaterialProvenance
from yieldforge.temporal_benchmark.contracts import (
    CandidateArchiveRequirement,
    TemporalPartition,
    TemporalRegime,
)


class BaselineContractModel(BaseModel):
    """Immutable, finite, strict base for M7 evidence."""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
    )


class ReusableGeometryProblem(BaselineContractModel):
    """One source-faithful geometry problem independent of temporal realization."""

    schema_version: Literal["yieldforge.m7-reusable-geometry-problem.v1"] = (
        "yieldforge.m7-reusable-geometry-problem.v1"
    )
    problem_id: StrictStr = Field(pattern=r"^yfm7p-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_catalog_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    tasks_index: StrictInt = Field(ge=0)
    sheet_type: StrictInt
    projection: SolverProjectionBinding
    problem: StripPackingProblem
    candidate_requirement: CandidateArchiveRequirement
    claim_ceiling: Literal[
        "reusable_source_geometry_and_solver_requirement_only_not_temporal_material_or_policy_"
        "evidence"
    ] = (
        "reusable_source_geometry_and_solver_requirement_only_not_temporal_material_or_policy_"
        "evidence"
    )

    @model_validator(mode="after")
    def require_content_identity(self) -> Self:
        digest = semantic_sha256(self, excluded_fields={"problem_id", "content_sha256"})
        if self.content_sha256 != f"sha256:{digest}":
            raise ValueError("reusable problem content SHA-256 does not match semantic content")
        if self.problem_id != f"yfm7p-{digest[:24]}":
            raise ValueError("reusable problem ID does not match semantic content")
        if self.problem.name != f"lectra-task-{self.tasks_index}":
            raise ValueError("reusable problem must preserve the catalog problem name")
        return self


class TemporalInstanceBinding(BaselineContractModel):
    """One auditable M6 event bound to reusable geometry without temporal leakage."""

    schema_version: Literal["yieldforge.m7-temporal-instance-binding.v1"] = (
        "yieldforge.m7-temporal-instance-binding.v1"
    )
    binding_id: StrictStr = Field(pattern=r"^yfm7b-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    problem_id: StrictStr = Field(pattern=r"^yfm7p-[0-9a-f]{24}$")
    problem_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    stream_id: StrictStr = Field(pattern=r"^yfts-[0-9a-f]{24}$")
    stream_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    event_id: StrictStr = Field(pattern=r"^yfte-[0-9a-f]{20}$")
    m6_batch_id: StrictStr = Field(pattern=r"^yftb-[0-9a-f]{20}$")
    m6_batch_sequence: StrictInt = Field(ge=0)
    m6_subsequence: StrictInt = Field(ge=0)
    sequence: StrictInt = Field(ge=0)
    tasks_index: StrictInt = Field(ge=0)
    released_at: datetime
    material: MaterialIdentity
    regime: TemporalRegime
    temporal_seed: StrictInt
    partition: TemporalPartition
    decomposition_rule: Literal["source_event_boundary_before_policy"] = (
        "source_event_boundary_before_policy"
    )
    chronology_provenance: Literal["generated"] = "generated"
    material_provenance: Literal["assumed"] = "assumed"

    @field_validator("released_at")
    @classmethod
    def canonicalize_release(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("instance release must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def require_content_identity(self) -> Self:
        if self.material.provenance is not MaterialProvenance.ASSUMED:
            raise ValueError("M7 temporal material must remain explicitly assumed")
        digest = semantic_sha256(self, excluded_fields={"binding_id", "content_sha256"})
        if self.content_sha256 != f"sha256:{digest}":
            raise ValueError("temporal binding content SHA-256 does not match semantic content")
        if self.binding_id != f"yfm7b-{digest[:24]}":
            raise ValueError("temporal binding ID does not match semantic content")
        return self


class M7ProblemIndex(BaselineContractModel):
    """Complete corrected problem census and instance binding for the M6 population."""

    schema_version: Literal["yieldforge.m7-problem-index.v1"] = "yieldforge.m7-problem-index.v1"
    index_id: StrictStr = Field(pattern=r"^yfm7i-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m6_contract_id: StrictStr = Field(pattern=r"^yfm6-[0-9a-f]{24}$")
    m6_contract_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m6_population_id: StrictStr = Field(pattern=r"^yftp-[0-9a-f]{24}$")
    m6_population_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_catalog_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    m6_batch_count: StrictInt = Field(ge=1)
    instance_count: StrictInt = Field(ge=1)
    problem_count: StrictInt = Field(ge=1)
    calibration_instance_count: StrictInt = Field(ge=1)
    calibration_problem_count: StrictInt = Field(ge=1)
    evaluation_instance_count: StrictInt = Field(ge=1)
    evaluation_problem_count: StrictInt = Field(ge=1)
    shared_problem_count: StrictInt = Field(ge=0)
    problems: tuple[ReusableGeometryProblem, ...] = Field(min_length=1)
    instances: tuple[TemporalInstanceBinding, ...] = Field(min_length=1)
    claim_ceiling: Literal[
        "candidate_problem_and_temporal_binding_population_only_not_action_policy_or_savings_"
        "evidence"
    ] = (
        "candidate_problem_and_temporal_binding_population_only_not_action_policy_or_savings_"
        "evidence"
    )

    @model_validator(mode="after")
    def require_complete_census_and_identity(self) -> Self:
        if self.problem_count != len(self.problems):
            raise ValueError("M7 problem count does not reconcile")
        if self.instance_count != len(self.instances):
            raise ValueError("M7 instance count does not reconcile")
        problem_ids = tuple(item.problem_id for item in self.problems)
        if problem_ids != tuple(sorted(set(problem_ids))):
            raise ValueError("M7 problems must use sorted unique identities")
        problem_by_id = {item.problem_id: item for item in self.problems}
        binding_ids = tuple(item.binding_id for item in self.instances)
        if len(binding_ids) != len(set(binding_ids)):
            raise ValueError("M7 temporal binding identities must be unique")
        for item in self.instances:
            problem = problem_by_id.get(item.problem_id)
            if problem is None or item.problem_sha256 != problem.content_sha256:
                raise ValueError("M7 instance does not bind one indexed problem")
            if item.tasks_index != problem.tasks_index:
                raise ValueError("M7 instance task does not match reusable problem")
        calibration = tuple(
            item for item in self.instances if item.partition is TemporalPartition.CALIBRATION
        )
        evaluation = tuple(
            item for item in self.instances if item.partition is TemporalPartition.EVALUATION
        )
        calibration_problems = {item.problem_id for item in calibration}
        evaluation_problems = {item.problem_id for item in evaluation}
        observed = (
            len(calibration),
            len(calibration_problems),
            len(evaluation),
            len(evaluation_problems),
            len(calibration_problems & evaluation_problems),
        )
        expected = (
            self.calibration_instance_count,
            self.calibration_problem_count,
            self.evaluation_instance_count,
            self.evaluation_problem_count,
            self.shared_problem_count,
        )
        if observed != expected:
            raise ValueError("M7 partition census does not reconcile")
        if (
            self.m6_batch_count,
            self.instance_count,
            self.problem_count,
            self.calibration_instance_count,
            self.calibration_problem_count,
            self.evaluation_instance_count,
            self.evaluation_problem_count,
            self.shared_problem_count,
        ) != (1024, 1152, 209, 288, 90, 864, 198, 79):
            raise ValueError("M7 registered corrected census differs from frozen values")
        digest = semantic_sha256(self, excluded_fields={"index_id", "content_sha256"})
        if self.content_sha256 != f"sha256:{digest}":
            raise ValueError("M7 problem index content SHA-256 does not match semantic content")
        if self.index_id != f"yfm7i-{digest[:24]}":
            raise ValueError("M7 problem index ID does not match semantic content")
        return self
