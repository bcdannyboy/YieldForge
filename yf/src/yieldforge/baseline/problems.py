"""Build reusable M7 problems and auditable bindings from the canonical M6 population."""

from __future__ import annotations

from pathlib import Path

from yieldforge.baseline.contracts import (
    M7ProblemIndex,
    ReusableGeometryProblem,
    TemporalInstanceBinding,
)
from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.temporal_benchmark.catalog import load_registered_catalog
from yieldforge.temporal_benchmark.contracts import (
    TemporalBenchmarkContract,
    build_registered_contract,
)
from yieldforge.temporal_benchmark.generator import TemporalStreamManifest
from yieldforge.temporal_benchmark.lowering import (
    LoweredProjection,
    LoweredReplayBatch,
    lower_stream,
)
from yieldforge.temporal_benchmark.population import (
    TemporalPopulationManifest,
)

_PACKAGE_ROOT = Path(__file__).resolve().parents[3]
_CONTRACT_PATH = _PACKAGE_ROOT / "benchmarks/temporal/m6-contract-v1.json"
_POPULATION_PATH = _PACKAGE_ROOT / "benchmarks/temporal/m6-population-v1.json"
_STREAM_ROOT = _PACKAGE_ROOT / "benchmarks/temporal/streams"


def _reusable_problem(
    *,
    catalog_sha256: str,
    candidate_requirement,  # type: ignore[no-untyped-def]
    batch: LoweredReplayBatch,
    projection: LoweredProjection,
    projected,  # type: ignore[no-untyped-def]
) -> ReusableGeometryProblem:
    if (
        projected.projection.projection_sha256 != projection.projection_sha256
        or projected.projection.assumption_codes != projection.assumption_codes
        or projected.projection.source_flip_part_count != projection.source_flip_part_count
        or len(projected.problem.parts) != projection.part_count
        or projected.problem.sheet_length != batch.sheet_length
        or projected.problem.strip_height != batch.sheet_width
    ):
        raise ValueError("M6 projection does not match the registered source problem")
    semantic = {
        "schema_version": "yieldforge.m7-reusable-geometry-problem.v1",
        "source_catalog_sha256": catalog_sha256,
        "tasks_index": projection.tasks_index,
        "sheet_type": batch.sheet_type,
        "projection": projected.projection.model_dump(mode="json"),
        "problem": projected.problem.model_dump(mode="json"),
        "candidate_requirement": candidate_requirement.model_dump(mode="json"),
        "claim_ceiling": (
            "reusable_source_geometry_and_solver_requirement_only_not_temporal_material_or_"
            "policy_evidence"
        ),
    }
    digest = semantic_sha256(semantic)
    return ReusableGeometryProblem(
        problem_id=f"yfm7p-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        source_catalog_sha256=catalog_sha256,
        tasks_index=projection.tasks_index,
        sheet_type=batch.sheet_type,
        projection=projected.projection,
        problem=projected.problem,
        candidate_requirement=candidate_requirement,
    )


def _instance_binding(
    *,
    stream: TemporalStreamManifest,
    batch: LoweredReplayBatch,
    subsequence: int,
    problem: ReusableGeometryProblem,
) -> TemporalInstanceBinding:
    event_id = batch.event_ids[subsequence]
    event = next(item for item in stream.events if item.event_id == event_id)
    semantic = {
        "schema_version": "yieldforge.m7-temporal-instance-binding.v1",
        "problem_id": problem.problem_id,
        "problem_sha256": problem.content_sha256,
        "stream_id": stream.stream_id,
        "stream_sha256": stream.content_sha256,
        "event_id": event.event_id,
        "m6_batch_id": batch.batch_id,
        "m6_batch_sequence": batch.sequence,
        "m6_subsequence": subsequence,
        "sequence": event.sequence,
        "tasks_index": event.source_task.tasks_index,
        "released_at": event.occurred_at.isoformat().replace("+00:00", "Z"),
        "material": event.material.model_dump(mode="json"),
        "regime": stream.regime,
        "temporal_seed": stream.seed,
        "partition": stream.partition,
        "decomposition_rule": "source_event_boundary_before_policy",
        "chronology_provenance": "generated",
        "material_provenance": "assumed",
    }
    digest = semantic_sha256(semantic)
    return TemporalInstanceBinding(
        binding_id=f"yfm7b-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        problem_id=problem.problem_id,
        problem_sha256=problem.content_sha256,
        stream_id=stream.stream_id,
        stream_sha256=stream.content_sha256,
        event_id=event.event_id,
        m6_batch_id=batch.batch_id,
        m6_batch_sequence=batch.sequence,
        m6_subsequence=subsequence,
        sequence=event.sequence,
        tasks_index=event.source_task.tasks_index,
        released_at=event.occurred_at,
        material=event.material,
        regime=stream.regime,
        temporal_seed=stream.seed,
        partition=stream.partition,
    )


def build_registered_problem_index() -> M7ProblemIndex:
    """Regenerate the complete registered M7 problem and instance index."""

    contract = TemporalBenchmarkContract.model_validate_json(
        _CONTRACT_PATH.read_bytes(), strict=True
    )
    if contract != build_registered_contract():
        raise ValueError("committed M6 contract differs from the registered builder")
    population = TemporalPopulationManifest.model_validate_json(
        _POPULATION_PATH.read_bytes(), strict=True
    )
    if (
        population.contract_id != contract.contract_id
        or population.contract_sha256 != contract.content_sha256
        or population.failed_cells
    ):
        raise ValueError("committed M6 population is not a complete binding to its contract")
    catalog = load_registered_catalog()
    problems: dict[str, ReusableGeometryProblem] = {}
    instances: list[TemporalInstanceBinding] = []
    batch_count = 0

    for record in population.streams:
        stream = TemporalStreamManifest.model_validate_json(
            (_STREAM_ROOT / record.filename).read_bytes(), strict=True
        )
        if (
            stream.stream_id != record.stream_id
            or stream.content_sha256 != record.stream_sha256
            or (stream.regime, stream.seed, stream.partition)
            != (record.regime, record.seed, record.partition)
        ):
            raise ValueError("M6 stream does not match its population record")
        report = lower_stream(contract, stream, catalog)
        batch_count += len(report.batches)
        for batch in report.batches:
            for subsequence, projection in enumerate(batch.projections):
                projected = catalog.project(projection.tasks_index)
                problem = _reusable_problem(
                    catalog_sha256=catalog.artifact_sha256,
                    candidate_requirement=contract.candidate_requirement,
                    batch=batch,
                    projection=projection,
                    projected=projected,
                )
                prior = problems.setdefault(problem.problem_id, problem)
                if prior != problem:
                    raise ValueError("reusable problem identity collision")
                instances.append(
                    _instance_binding(
                        stream=stream,
                        batch=batch,
                        subsequence=subsequence,
                        problem=problem,
                    )
                )

    ordered_problems = tuple(sorted(problems.values(), key=lambda item: item.problem_id))
    calibration = tuple(item for item in instances if item.partition.value == "calibration")
    evaluation = tuple(item for item in instances if item.partition.value == "evaluation")
    calibration_problem_ids = {item.problem_id for item in calibration}
    evaluation_problem_ids = {item.problem_id for item in evaluation}
    semantic = {
        "schema_version": "yieldforge.m7-problem-index.v1",
        "m6_contract_id": contract.contract_id,
        "m6_contract_sha256": contract.content_sha256,
        "m6_population_id": population.population_id,
        "m6_population_sha256": population.content_sha256,
        "source_catalog_sha256": catalog.artifact_sha256,
        "m6_batch_count": batch_count,
        "instance_count": len(instances),
        "problem_count": len(ordered_problems),
        "calibration_instance_count": len(calibration),
        "calibration_problem_count": len(calibration_problem_ids),
        "evaluation_instance_count": len(evaluation),
        "evaluation_problem_count": len(evaluation_problem_ids),
        "shared_problem_count": len(calibration_problem_ids & evaluation_problem_ids),
        "problems": [item.model_dump(mode="json") for item in ordered_problems],
        "instances": [item.model_dump(mode="json") for item in instances],
        "claim_ceiling": (
            "candidate_problem_and_temporal_binding_population_only_not_action_policy_or_"
            "savings_evidence"
        ),
    }
    digest = semantic_sha256(semantic)
    return M7ProblemIndex(
        index_id=f"yfm7i-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        m6_contract_id=contract.contract_id,
        m6_contract_sha256=contract.content_sha256,
        m6_population_id=population.population_id,
        m6_population_sha256=population.content_sha256,
        source_catalog_sha256=catalog.artifact_sha256,
        m6_batch_count=batch_count,
        instance_count=len(instances),
        problem_count=len(ordered_problems),
        calibration_instance_count=len(calibration),
        calibration_problem_count=len(calibration_problem_ids),
        evaluation_instance_count=len(evaluation),
        evaluation_problem_count=len(evaluation_problem_ids),
        shared_problem_count=len(calibration_problem_ids & evaluation_problem_ids),
        problems=ordered_problems,
        instances=tuple(instances),
    )
