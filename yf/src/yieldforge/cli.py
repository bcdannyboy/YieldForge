"""Command-line entry points for the YieldForge experiment loop."""

import argparse
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from yieldforge.archive import CandidateArchive
from yieldforge.baseline.experiment import (
    M7CollisionDifferentialResult,
    M7FeasibilityResult,
    M7FrozenBaseline,
    execute_calibration,
    execute_collision_differential_probe,
    execute_evaluation,
    execute_feasibility_slice,
    publish_calibration_result,
    publish_collision_differential_result,
    publish_evaluation_result,
    publish_feasibility_result,
    publish_frozen_baseline,
    publish_problem_index,
)
from yieldforge.baseline.problems import (
    build_registered_calibration_problem_view,
    build_registered_problem_index,
)
from yieldforge.datasets.fetch import fetch_file
from yieldforge.datasets.passive_report import (
    PassiveEvidenceError,
    load_lectra_audit_evidence,
)
from yieldforge.datasets.postgres_catalog import import_catalog
from yieldforge.datasets.source_manifest import DatasetSourceManifest
from yieldforge.domain import SpyrrowRunConfig, StripPackingProblem
from yieldforge.experiments.calibration import (
    CalibrationApiClient,
    build_geometry_confirmation_result,
    load_geometry_confirmation_result,
    orchestrate_calibration,
    orchestrate_confirmation,
    publish_geometry_confirmation_result,
    registered_cells,
    registered_confirmation_cells,
)
from yieldforge.experiments.contracts import (
    M0ExperimentContract,
    PureGeometryConfirmationProtocol,
    load_frozen_json,
    validate_experiment_bundle,
)
from yieldforge.experiments.deterministic_replay import (
    evaluate_m5_replay,
    load_m5_replay_input_unbound,
    prepare_m5_replay_input,
    publish_m5_replay_input,
    publish_m5_replay_result,
)
from yieldforge.experiments.remnant_reuse import (
    REGISTERED_M4_SEARCH_CONFIG,
    evaluate_m4_remnant_reuse,
    load_m4_input_pack,
    load_m4_result,
    prepare_m4_input_pack,
    publish_m4_input_pack,
    publish_m4_result,
)
from yieldforge.experiments.residual_geometry import (
    evaluate_m3_residual_geometry,
    load_m3_input_pack,
    load_m3_result,
    prepare_m3_input_pack,
    publish_m3_input_pack,
    publish_m3_result,
)
from yieldforge.oracle.experiment import (
    execute_certificate_profile,
    execute_portable_fact_gate3,
    execute_sparse_prefix_proof,
    publish_certificate_profile,
    publish_portable_fact_gate3,
    publish_sparse_proof,
)
from yieldforge.spyrrow_adapter import SpyrrowAdapter
from yieldforge.temporal_benchmark.catalog import load_registered_catalog
from yieldforge.temporal_benchmark.contracts import TemporalRegime, build_registered_contract
from yieldforge.temporal_benchmark.pilot import publish_pilot_result, run_lowering_pilot
from yieldforge.temporal_benchmark.population import (
    build_population,
    publish_population_artifacts,
    validate_population_artifacts,
)

DatasetAuditCheckError = PassiveEvidenceError


def _generate_candidates(args: argparse.Namespace) -> int:
    problem = StripPackingProblem.model_validate_json(args.input.read_text())
    config = SpyrrowRunConfig(
        seed=args.seed,
        total_computation_time=args.seconds,
        early_termination=args.early_termination,
        num_workers=args.workers,
        min_items_separation=args.min_separation,
    )
    batch = SpyrrowAdapter().generate(problem, config)
    CandidateArchive.create(args.output, batch)
    noun = "candidate" if len(batch.candidates) == 1 else "candidates"
    print(f"Archived {len(batch.candidates)} {noun} to {args.output}")
    return 0


def _fetch_dataset(args: argparse.Namespace) -> int:
    manifest = DatasetSourceManifest.model_validate_json(args.manifest.read_text())
    for spec in manifest.files:
        status = fetch_file(spec, args.output / spec.name)
        print(f"{spec.name}: {status.value}")
    return 0


def _check_dataset_audit(args: argparse.Namespace) -> int:
    report, _ = load_lectra_audit_evidence(args.report, args.manifest)

    rows = report.table_rows
    print(
        f"Validated {report.dataset_id} audit: "
        f"tasks={rows['tasks']}, parts={rows['parts']}, "
        f"shapes={rows['shapes']}, constraints={rows['constraints']}"
    )
    return 0


def _import_dataset_catalog(args: argparse.Namespace) -> int:
    catalog_manifest = args.catalog_manifest or args.catalog.parent / "catalog-manifest.json"
    result = import_catalog(
        database_url=args.database_url,
        catalog_path=args.catalog,
        catalog_manifest_path=catalog_manifest,
        source_manifest_path=args.source_manifest,
        audit_report_path=args.audit_report,
    )
    print(
        f"Imported {result.task_count} catalog tasks with artifact SHA-256 {result.catalog_sha256}"
    )
    return 0


def _validate_experiment_contracts(args: argparse.Namespace) -> int:
    bundle = validate_experiment_bundle(
        m0_path=args.m0,
        geometry_path=args.geometry,
        catalog_path=args.catalog,
        catalog_manifest_path=args.catalog_manifest,
    )
    geometry = bundle.geometry
    confirmation = "enabled" if geometry.confirmation_enabled else "disabled"
    print(
        "Validated experiment bundle: "
        f"M0={bundle.m0.contract_id} geometry={geometry.protocol_id} "
        f"catalog={bundle.catalog_sha256} "
        f"calibration={len(geometry.split.calibration_task_ids)} "
        f"evaluation={len(geometry.split.evaluation_task_ids)} "
        f"confirmation={confirmation}"
    )
    return 0


def _calibrate_geometry_api(args: argparse.Namespace) -> int:
    bundle = validate_experiment_bundle(
        m0_path=args.m0,
        geometry_path=args.geometry,
        catalog_path=args.catalog,
        catalog_manifest_path=args.catalog_manifest,
    )
    protocol = bundle.geometry
    if protocol.confirmation_enabled or protocol.budget.selected_seconds_per_seed is not None:
        raise ValueError("calibration requires the frozen confirmation-disabled protocol")
    cells = registered_cells(protocol)
    client = CalibrationApiClient(args.api_origin)
    client.require_corpus(
        dataset_id=protocol.references.dataset_id,
        catalog_sha256=bundle.catalog_sha256,
        task_count=(
            len(protocol.population.eligible_task_ids) + len(protocol.population.blocked_tasks)
        ),
        eligible_task_count=len(protocol.population.eligible_task_ids),
    )
    if args.preflight_only:
        print(
            "Validated registered geometry calibration: "
            f"protocol={protocol.protocol_id} registered_cells={len(cells)} "
            f"api={client.origin}"
        )
        return 0

    def report_progress(completed: int, total: int, evidence) -> None:  # type: ignore[no-untyped-def]
        if completed == 1 or completed % 25 == 0 or completed == total:
            archive = "valid" if evidence.archive_valid else "invalid"
            print(f"Calibration progress: {completed}/{total} latest_archive={archive}")

    result = orchestrate_calibration(
        protocol=protocol,
        client=client,
        output_root=args.output,
        progress=report_progress,
    )
    selected = result.evaluation.selected_seconds_per_seed
    print(
        "Calibration finished: "
        f"valid={str(result.evaluation.valid).lower()} "
        f"selected_seconds_per_seed={selected} output={args.output}"
    )
    return 0


def _confirm_geometry_api(args: argparse.Namespace) -> int:
    protocol = load_frozen_json(args.geometry, PureGeometryConfirmationProtocol)
    cells = registered_confirmation_cells(protocol)
    client = CalibrationApiClient(args.api_origin)
    client.require_corpus(
        dataset_id=protocol.references.dataset_id,
        catalog_sha256=protocol.references.catalog_artifact_sha256,
        task_count=(
            len(protocol.population.eligible_task_ids) + len(protocol.population.blocked_tasks)
        ),
        eligible_task_count=len(protocol.population.eligible_task_ids),
    )
    print(
        "Validated registered geometry confirmation: "
        f"protocol={protocol.protocol_id} registered_cells={len(cells)} api={client.origin}"
    )
    if args.preflight_only:
        return 0

    def report_progress(completed: int, total: int, evidence) -> None:  # type: ignore[no-untyped-def]
        if completed == 1 or completed % 25 == 0 or completed == total:
            archive = "valid" if evidence.archive_valid else "invalid"
            print(f"Confirmation progress: {completed}/{total} latest_archive={archive}")

    result = orchestrate_confirmation(
        protocol=protocol,
        client=client,
        output_root=args.output,
        progress=report_progress,
    )
    if args.result_output is not None:
        canonical = build_geometry_confirmation_result(protocol, result)
        publish_geometry_confirmation_result(args.result_output, canonical)
        print(
            "Published canonical geometry confirmation: "
            f"result_id={canonical.result_id} output={args.result_output}"
        )
    evaluation = result.evaluation
    print(
        "Confirmation finished: "
        f"decision={evaluation.decision} "
        f"qualifying_task_rate_percent={evaluation.qualifying_task_rate_percent} "
        f"valid_archive_rate_percent={evaluation.valid_archive_rate_percent} "
        f"output={args.output}"
    )
    return 0


def _prepare_residual_geometry(args: argparse.Namespace) -> int:
    confirmation = load_geometry_confirmation_result(args.confirmation_result)
    m0 = load_frozen_json(args.m0, M0ExperimentContract)
    pack = prepare_m3_input_pack(confirmation, m0, args.archive_root)
    path = publish_m3_input_pack(args.output, pack)
    print(
        "Published M3 residual input: "
        f"input_id={pack.input_id} task_pairs={len(pack.expected_task_ids)} output={path}"
    )
    return 0


def _evaluate_residual_geometry(args: argparse.Namespace) -> int:
    pack = load_m3_input_pack(args.input)
    m0 = load_frozen_json(args.m0, M0ExperimentContract)
    result = evaluate_m3_residual_geometry(pack, m0)
    path = publish_m3_result(args.output, result)
    summary = result.summary
    print(
        "Published M3 residual result: "
        f"result_id={result.result_id} "
        f"valid_tasks={summary.valid_task_count}/{summary.registered_task_count} "
        f"exact_residual_differences={summary.exact_residual_difference_count} "
        f"decision={summary.technical_decision} output={path}"
    )
    return 0


def _prepare_remnant_reuse(args: argparse.Namespace) -> int:
    m3_input = load_m3_input_pack(args.m3_input)
    m3_result = load_m3_result(args.m3_result)
    m0 = load_frozen_json(args.m0, M0ExperimentContract)
    pack = prepare_m4_input_pack(
        m3_input,
        m3_result,
        m0,
        search_config=REGISTERED_M4_SEARCH_CONFIG,
    )
    path = publish_m4_input_pack(args.output, pack)
    print(
        "Published M4 remnant reuse input: "
        f"input_id={pack.input_id} "
        f"origin_remnants={len(pack.origin_remnants)} "
        f"future_parts={len(pack.future_part_roles)} output={path}"
    )
    return 0


def _evaluate_remnant_reuse(args: argparse.Namespace) -> int:
    pack = load_m4_input_pack(args.input)
    m0 = load_frozen_json(args.m0, M0ExperimentContract)
    result = evaluate_m4_remnant_reuse(pack, m0)
    path = publish_m4_result(args.output, result)
    summary = result.summary
    print(
        "Published M4 remnant reuse result: "
        f"result_id={result.result_id} "
        f"attempted_pairs={summary.attempted_pair_count}/{summary.eligible_pair_count} "
        f"decision={summary.technical_decision} "
        f"avoided_full_sheet_openings={summary.avoided_full_sheet_openings} output={path}"
    )
    return 0


def _prepare_deterministic_replay(args: argparse.Namespace) -> int:
    m0 = load_frozen_json(args.m0, M0ExperimentContract)
    m4_pack = load_m4_input_pack(args.m4_input)
    m4_result = load_m4_result(args.m4_result, pack=m4_pack, m0=m0)
    replay_input = prepare_m5_replay_input(m4_pack, m4_result, m0)
    path = publish_m5_replay_input(args.output, replay_input)
    print(
        "Published M5 deterministic replay input: "
        f"input_id={replay_input.input_id} orders={len(replay_input.orders)} "
        f"horizon_end={replay_input.horizon_end} output={path}"
    )
    return 0


def _evaluate_deterministic_replay(args: argparse.Namespace) -> int:
    replay_input = load_m5_replay_input_unbound(args.input)
    m0 = load_frozen_json(args.m0, M0ExperimentContract)
    result = evaluate_m5_replay(replay_input, m0)
    path = publish_m5_replay_result(args.output, result)
    summary = result.summary
    print(
        "Published M5 deterministic replay result: "
        f"result_id={result.result_id} "
        f"fulfilled={summary.fulfilled_order_count}/{summary.order_count} "
        f"final_net_cost={summary.final_net_cost} "
        f"decision={summary.technical_decision} output={path}"
    )
    return 0


def _generate_m6_benchmark(args: argparse.Namespace) -> int:
    contract = build_registered_contract()
    catalog = load_registered_catalog()
    population, streams = build_population(contract, catalog)
    paths = publish_population_artifacts(
        contract_path=args.contract,
        population_path=args.population,
        stream_root=args.stream_root,
        contract=contract,
        population=population,
        streams=streams,
    )
    print(
        "Published M6 temporal benchmark: "
        f"contract={contract.contract_id} population={population.population_id} "
        f"streams={population.stream_count}/{population.registered_cell_count} "
        f"failures={len(population.failed_cells)} output={paths.population_path}"
    )
    return 0 if not population.failed_cells else 1


def _validate_m6_benchmark(args: argparse.Namespace) -> int:
    summary = validate_population_artifacts(
        contract_path=args.contract,
        population_path=args.population,
        stream_root=args.stream_root,
    )
    print(
        "Validated M6 temporal benchmark: "
        f"contract={summary.contract_id} population={summary.population_id} "
        f"streams={summary.stream_count} events={summary.event_count} "
        f"batches={summary.batch_count} parts={summary.part_count}"
    )
    return 0


def _pilot_m6_benchmark(args: argparse.Namespace) -> int:
    validate_population_artifacts(
        contract_path=args.contract,
        population_path=args.population,
        stream_root=args.stream_root,
    )
    contract = build_registered_contract()
    catalog = load_registered_catalog()
    population, streams = build_population(contract, catalog)
    result = run_lowering_pilot(contract, population, streams, catalog)
    path = publish_pilot_result(args.output, result)
    print(
        "Published M6 lowering pilot: "
        f"result={result.result_id} streams={len(result.streams)} "
        f"events={result.event_count} batches={result.batch_count} parts={result.part_count} "
        f"fit_search_calls={result.exact_fit_search_call_count} "
        f"collision_backend={result.collision_backend_decision} output={path}"
    )
    return 0


def _index_m7_baseline(args: argparse.Namespace) -> int:
    index = build_registered_problem_index()
    path = publish_problem_index(args.output, index)
    print(
        "Published M7 problem index: "
        f"index={index.index_id} instances={index.instance_count} "
        f"problems={index.problem_count} calibration_problems={index.calibration_problem_count} "
        f"evaluation_problems={index.evaluation_problem_count} output={path}"
    )
    return 0


def _pilot_m7_baseline(args: argparse.Namespace) -> int:
    index = build_registered_problem_index()
    m0 = load_frozen_json(args.m0, M0ExperimentContract)
    collision_differential = (
        M7CollisionDifferentialResult.model_validate_json(
            args.collision_differential.read_bytes(), strict=True
        )
        if args.collision_differential is not None
        else None
    )

    def progress(message: str) -> None:
        print(f"M7 pilot: {message}")

    result = execute_feasibility_slice(
        index=index,
        m0=m0,
        archive_roots=tuple(args.archive_root),
        jagua_executable=args.jagua_binary,
        collision_differential=collision_differential,
        progress=progress,
    )
    path = publish_feasibility_result(args.output, result)
    gate = result.collision_gate
    print(
        "Published M7 feasibility result: "
        f"result={result.result_id} streams={result.stream_count} "
        f"instances={result.instance_count} actions={result.total_action_count} "
        f"fit_search_share={gate.fit_search_share} "
        f"projected_calibration_minutes={gate.projected_calibration_minutes} "
        f"collision_backend={gate.decision.value} output={path}"
    )
    return 0


def _probe_m7_collision_backend(args: argparse.Namespace) -> int:
    index = build_registered_problem_index()
    m0 = load_frozen_json(args.m0, M0ExperimentContract)

    def progress(message: str) -> None:
        print(f"M7 collision probe: {message}")

    result = execute_collision_differential_probe(
        index=index,
        m0=m0,
        archive_roots=tuple(args.archive_root),
        jagua_executable=args.jagua_binary,
        progress=progress,
    )
    path = publish_collision_differential_result(args.output, result)
    print(
        "Published M7 collision differential: "
        f"result={result.result_id} queries={result.jagua_guarded_query_count} "
        f"rejections={result.jagua_rejection_count} "
        f"mismatches={result.jagua_audit_mismatch_count} "
        f"search_speedup={result.measured_search_speedup} "
        f"backend_speedup={result.measured_backend_speedup} "
        f"decision={result.technical_decision} output={path}"
    )
    return 0


def _calibrate_m7_baseline(args: argparse.Namespace) -> int:
    index = build_registered_problem_index()
    m0 = load_frozen_json(args.m0, M0ExperimentContract)
    collision_differential = M7CollisionDifferentialResult.model_validate_json(
        args.collision_differential.read_bytes(), strict=True
    )
    feasibility = M7FeasibilityResult.model_validate_json(
        args.feasibility.read_bytes(), strict=True
    )

    def progress(message: str) -> None:
        print(f"M7 calibration: {message}")

    result, frozen = execute_calibration(
        index=index,
        m0=m0,
        archive_roots=tuple(args.archive_root),
        jagua_executable=args.jagua_binary,
        collision_differential=collision_differential,
        feasibility=feasibility,
        progress=progress,
    )
    result_path = publish_calibration_result(args.output, result)
    freeze_path = publish_frozen_baseline(args.output, frozen)
    print(
        "Published M7 calibration and frozen baseline: "
        f"result={result.result_id} winner={result.winning_policy.name.value} "
        f"streams={len(result.streams)} replay_seconds={result.total_replay_seconds} "
        f"result_output={result_path} freeze_output={freeze_path}"
    )
    return 0


def _evaluate_m7_baseline(args: argparse.Namespace) -> int:
    index = build_registered_problem_index()
    m0 = load_frozen_json(args.m0, M0ExperimentContract)
    frozen = M7FrozenBaseline.model_validate_json(
        args.frozen_baseline.read_bytes(), strict=True
    )

    def progress(message: str) -> None:
        print(f"M7 evaluation: {message}")

    result = execute_evaluation(
        index=index,
        m0=m0,
        frozen=frozen,
        archive_roots=tuple(args.archive_root),
        jagua_executable=args.jagua_binary,
        progress=progress,
    )
    result_path = publish_evaluation_result(args.output, result)
    print(
        "Published M7 frozen-policy evaluation: "
        f"result={result.result_id} policy={result.frozen_policy.name.value} "
        f"streams={result.stream_count} instances={result.instance_count} "
        f"repeat_match={str(result.repeat_content_identity_match).lower()} "
        f"mean_final_net_cost={result.mean_final_net_cost} output={result_path}"
    )
    return 0


def _prove_m8_sparse_oracle(args: argparse.Namespace) -> int:
    m0 = load_frozen_json(args.m0, M0ExperimentContract)
    frozen = M7FrozenBaseline.model_validate_json(
        args.frozen_baseline.read_bytes(), strict=True
    )
    index = build_registered_calibration_problem_view(
        full_problem_index_id=frozen.problem_index_id,
        full_problem_index_sha256=frozen.problem_index_sha256,
    )

    def progress(message: str) -> None:
        print(f"M8 certificate proof: {message}", flush=True)

    result = execute_sparse_prefix_proof(
        index=index,
        m0=m0,
        frozen=frozen,
        archive_roots=tuple(args.archive_root),
        jagua_executable=args.jagua_binary,
        progress=progress,
    )
    result_path = publish_sparse_proof(args.output, result)
    print(
        "Published M8 certificate proof: "
        f"proof={result.proof_id} cells={result.completed_cell_count}/6 "
        f"valid_proofs={result.valid_proof_count}/{result.current_action_count} "
        f"checker_failures={result.checker_failure_count} "
        f"audit_mismatches={result.audit_mismatch_count} "
        f"sampled_speedup={result.sampled_speedup} "
        f"measured_processes={result.measured_process_count} "
        f"configured_workers={result.configured_worker_count} "
        f"pipeline_wall_seconds={result.certificate_pipeline_wall_seconds} "
        f"total_wall_seconds={result.total_wall_seconds} "
        f"projected_days={result.projected_held_out_calendar_days} "
        f"decision={result.technical_decision} output={result_path}"
    )
    return 0


def _run_m8_portable_fact_gate3(args: argparse.Namespace) -> int:
    """Run the frozen calibration-only portable-fact proof pipeline."""

    m0 = load_frozen_json(args.m0, M0ExperimentContract)
    frozen = load_frozen_json(args.frozen_baseline, M7FrozenBaseline)
    index = build_registered_calibration_problem_view(
        full_problem_index_id=frozen.problem_index_id,
        full_problem_index_sha256=frozen.problem_index_sha256,
    )

    def progress(message: str) -> None:
        print(f"M8 portable fact Gate-3: {message}", flush=True)

    result = execute_portable_fact_gate3(
        index=index,
        m0=m0,
        frozen=frozen,
        archive_roots=tuple(args.archive_root),
        jagua_executable=args.jagua_binary,
        progress=progress,
    )
    result_path = publish_portable_fact_gate3(args.output, result)
    roots = "+".join(str(cell.checked_action_root_count) for cell in result.cells)
    print(
        "Published M8 portable fact Gate-3: "
        f"gate3={result.gate3_id} "
        f"roots={roots}/{result.checked_action_root_count} "
        f"fallback={result.total_exact_fallback_count} "
        f"repeat={str(result.bundle_root_repeat_match).lower()} "
        f"compute={result.peak_compute_count}/{result.compute_slot_cap} "
        f"evaluation_accessed={str(result.evaluation_accessed).lower()} "
        f"first_generation_seconds={result.first_generation_phase_wall_seconds} "
        f"second_generation_seconds={result.second_generation_phase_wall_seconds} "
        f"checker_seconds={result.checker_phase_wall_seconds} "
        f"total_seconds={result.total_pipeline_wall_seconds} "
        f"decision={result.pipeline_decision} output={result_path}",
        flush=True,
    )
    return 0


def _profile_m8_certificate(args: argparse.Namespace) -> int:
    """Profile one explicit calibration stream without opening evaluation."""

    m0 = load_frozen_json(args.m0, M0ExperimentContract)
    frozen = M7FrozenBaseline.model_validate_json(
        args.frozen_baseline.read_bytes(), strict=True
    )
    index = build_registered_calibration_problem_view(
        full_problem_index_id=frozen.problem_index_id,
        full_problem_index_sha256=frozen.problem_index_sha256,
    )
    result = execute_certificate_profile(
        index=index,
        m0=m0,
        frozen=frozen,
        archive_roots=tuple(args.archive_root),
        jagua_executable=args.jagua_binary,
        regime=args.regime,
        temporal_seed=args.seed,
        event_count=args.event_count,
    )
    result_path = publish_certificate_profile(args.output, result)
    profile = cast(dict[str, object], result["profile"])
    print(
        "Published M8 certificate profile: "
        f"regime={result['regime']} seed={result['temporal_seed']} "
        f"events={result['event_count']} actions={result['action_count']} "
        f"accounted_process_fraction={profile['accounted_process_fraction']} "
        f"evaluation_accessed=false output={result_path}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="yieldforge")
    commands = parser.add_subparsers(dest="command", required=True)
    candidates = commands.add_parser("candidates", help="manage solver candidates")
    candidate_commands = candidates.add_subparsers(dest="candidate_command", required=True)
    generate = candidate_commands.add_parser("generate", help="generate a candidate archive")
    generate.add_argument("--input", type=Path, required=True, help="problem JSON path")
    generate.add_argument("--output", type=Path, required=True, help="new archive directory")
    generate.add_argument("--seed", type=int, default=0)
    generate.add_argument("--seconds", type=int, default=10)
    generate.add_argument("--workers", type=int, default=1)
    generate.add_argument("--min-separation", type=float)
    generate.add_argument("--early-termination", action="store_true")
    generate.set_defaults(handler=_generate_candidates)

    datasets = commands.add_parser("datasets", help="acquire and qualify external datasets")
    dataset_commands = datasets.add_subparsers(dest="dataset_command", required=True)
    fetch = dataset_commands.add_parser("fetch", help="download and verify a pinned dataset")
    fetch.add_argument("--manifest", type=Path, required=True, help="source manifest JSON path")
    fetch.add_argument("--output", type=Path, required=True, help="raw dataset directory")
    fetch.set_defaults(handler=_fetch_dataset)

    audit_check = dataset_commands.add_parser(
        "audit-check", help="validate a passive audit report against its pinned source"
    )
    audit_check.add_argument("--report", type=Path, required=True, help="audit report JSON path")
    audit_check.add_argument(
        "--manifest", type=Path, required=True, help="source manifest JSON path"
    )
    audit_check.set_defaults(handler=_check_dataset_audit)

    catalog_import = dataset_commands.add_parser(
        "catalog-import",
        help="validate and import a pinned catalog into PostgreSQL",
    )
    catalog_import.add_argument("--catalog", type=Path, required=True)
    catalog_import.add_argument("--catalog-manifest", type=Path)
    catalog_import.add_argument(
        "--manifest",
        "--source-manifest",
        dest="source_manifest",
        type=Path,
        required=True,
    )
    catalog_import.add_argument("--audit-report", type=Path, required=True)
    catalog_import.add_argument("--database-url", required=True)
    catalog_import.set_defaults(handler=_import_dataset_catalog)

    experiments = commands.add_parser("experiments", help="validate registered experiments")
    experiment_commands = experiments.add_subparsers(dest="experiment_command", required=True)
    validate = experiment_commands.add_parser(
        "validate",
        help="validate the frozen M0 and pure-geometry bundle",
    )
    validate.add_argument("--m0", type=Path, required=True)
    validate.add_argument("--geometry", type=Path, required=True)
    validate.add_argument("--catalog", type=Path, required=True)
    validate.add_argument("--catalog-manifest", type=Path, required=True)
    validate.set_defaults(handler=_validate_experiment_contracts)

    calibrate = experiment_commands.add_parser(
        "calibrate-geometry-api",
        help="execute or resume the registered geometry calibration through the API",
    )
    calibrate.add_argument("--m0", type=Path, required=True)
    calibrate.add_argument("--geometry", type=Path, required=True)
    calibrate.add_argument("--catalog", type=Path, required=True)
    calibrate.add_argument("--catalog-manifest", type=Path, required=True)
    calibrate.add_argument("--api-origin", required=True)
    calibrate.add_argument("--output", type=Path, required=True)
    calibrate.add_argument("--preflight-only", action="store_true")
    calibrate.set_defaults(handler=_calibrate_geometry_api)

    confirm = experiment_commands.add_parser(
        "confirm-geometry-api",
        help="execute or resume the registered 203-task geometry confirmation",
    )
    confirm.add_argument("--geometry", type=Path, required=True)
    confirm.add_argument("--api-origin", required=True)
    confirm.add_argument("--output", type=Path, required=True)
    confirm.add_argument("--result-output", type=Path)
    confirm.add_argument("--preflight-only", action="store_true")
    confirm.set_defaults(handler=_confirm_geometry_api)

    prepare_residual = experiment_commands.add_parser(
        "prepare-residual-geometry",
        help="verify M2 archives and freeze the exact M3 candidate pairs",
    )
    prepare_residual.add_argument("--m0", type=Path, required=True)
    prepare_residual.add_argument("--confirmation-result", type=Path, required=True)
    prepare_residual.add_argument("--archive-root", type=Path, required=True)
    prepare_residual.add_argument("--output", type=Path, required=True)
    prepare_residual.set_defaults(handler=_prepare_residual_geometry)

    evaluate_residual = experiment_commands.add_parser(
        "evaluate-residual-geometry",
        help="evaluate and publish the frozen exact M3 residual pairs",
    )
    evaluate_residual.add_argument("--m0", type=Path, required=True)
    evaluate_residual.add_argument("--input", type=Path, required=True)
    evaluate_residual.add_argument("--output", type=Path, required=True)
    evaluate_residual.set_defaults(handler=_evaluate_residual_geometry)

    prepare_reuse = experiment_commands.add_parser(
        "prepare-remnant-reuse",
        help="reconstruct M3 remnants and freeze the bounded M4 search input",
    )
    prepare_reuse.add_argument("--m0", type=Path, required=True)
    prepare_reuse.add_argument("--m3-input", type=Path, required=True)
    prepare_reuse.add_argument("--m3-result", type=Path, required=True)
    prepare_reuse.add_argument("--output", type=Path, required=True)
    prepare_reuse.set_defaults(handler=_prepare_remnant_reuse)

    evaluate_reuse = experiment_commands.add_parser(
        "evaluate-remnant-reuse",
        help="evaluate and publish the frozen bounded M4 remnant search",
    )
    evaluate_reuse.add_argument("--m0", type=Path, required=True)
    evaluate_reuse.add_argument("--input", type=Path, required=True)
    evaluate_reuse.add_argument("--output", type=Path, required=True)
    evaluate_reuse.set_defaults(handler=_evaluate_remnant_reuse)

    prepare_replay = experiment_commands.add_parser(
        "prepare-deterministic-replay",
        help="bind canonical M0/M4 evidence and freeze the generated M5 replay input",
    )
    prepare_replay.add_argument("--m0", type=Path, required=True)
    prepare_replay.add_argument("--m4-input", type=Path, required=True)
    prepare_replay.add_argument("--m4-result", type=Path, required=True)
    prepare_replay.add_argument("--output", type=Path, required=True)
    prepare_replay.set_defaults(handler=_prepare_deterministic_replay)

    evaluate_replay = experiment_commands.add_parser(
        "evaluate-deterministic-replay",
        help="execute and publish the frozen M5 chronological replay",
    )
    evaluate_replay.add_argument("--m0", type=Path, required=True)
    evaluate_replay.add_argument("--input", type=Path, required=True)
    evaluate_replay.add_argument("--output", type=Path, required=True)
    evaluate_replay.set_defaults(handler=_evaluate_deterministic_replay)

    benchmark = commands.add_parser("benchmark", help="manage registered benchmark artifacts")
    benchmark_commands = benchmark.add_subparsers(dest="benchmark_command", required=True)
    generate_m6 = benchmark_commands.add_parser(
        "m6-generate",
        help="generate and publish the registered M6 temporal population",
    )
    generate_m6.add_argument("--contract", type=Path, required=True)
    generate_m6.add_argument("--population", type=Path, required=True)
    generate_m6.add_argument("--stream-root", type=Path, required=True)
    generate_m6.set_defaults(handler=_generate_m6_benchmark)

    validate_m6 = benchmark_commands.add_parser(
        "m6-validate",
        help="regenerate, lower, and validate the registered M6 temporal population",
    )
    validate_m6.add_argument("--contract", type=Path, required=True)
    validate_m6.add_argument("--population", type=Path, required=True)
    validate_m6.add_argument("--stream-root", type=Path, required=True)
    validate_m6.set_defaults(handler=_validate_m6_benchmark)

    pilot_m6 = benchmark_commands.add_parser(
        "m6-pilot",
        help="profile a stratified M6 lowering and exact-geometry sample",
    )
    pilot_m6.add_argument("--contract", type=Path, required=True)
    pilot_m6.add_argument("--population", type=Path, required=True)
    pilot_m6.add_argument("--stream-root", type=Path, required=True)
    pilot_m6.add_argument("--output", type=Path, required=True)
    pilot_m6.set_defaults(handler=_pilot_m6_benchmark)

    index_m7 = benchmark_commands.add_parser(
        "m7-index",
        help="regenerate and publish the corrected reusable M7 problem index",
    )
    index_m7.add_argument("--output", type=Path, required=True)
    index_m7.set_defaults(handler=_index_m7_baseline)

    pilot_m7 = benchmark_commands.add_parser(
        "m7-pilot",
        help="verify shared M2 evidence and execute the six-regime M7 feasibility slice",
    )
    pilot_m7.add_argument("--m0", type=Path, required=True)
    pilot_m7.add_argument(
        "--archive-root",
        type=Path,
        action="append",
        required=True,
        help="candidate archive root; repeat for isolated M2 runtime roots",
    )
    pilot_m7.add_argument("--output", type=Path, required=True)
    pilot_m7.add_argument("--jagua-binary", type=Path)
    pilot_m7.add_argument("--collision-differential", type=Path)
    pilot_m7.set_defaults(handler=_pilot_m7_baseline)

    probe_m7 = benchmark_commands.add_parser(
        "m7-collision-probe",
        help="audit the pinned Jagua extension against the first real recurrence search",
    )
    probe_m7.add_argument("--m0", type=Path, required=True)
    probe_m7.add_argument(
        "--archive-root",
        type=Path,
        action="append",
        required=True,
        help="candidate archive root; repeat for isolated M2 runtime roots",
    )
    probe_m7.add_argument("--jagua-binary", type=Path, required=True)
    probe_m7.add_argument("--output", type=Path, required=True)
    probe_m7.set_defaults(handler=_probe_m7_collision_backend)

    calibrate_m7 = benchmark_commands.add_parser(
        "m7-calibrate",
        help="execute all calibration policies and freeze the selected M7 baseline",
    )
    calibrate_m7.add_argument("--m0", type=Path, required=True)
    calibrate_m7.add_argument(
        "--archive-root",
        type=Path,
        action="append",
        required=True,
        help="candidate archive root; repeat for isolated M2 runtime roots",
    )
    calibrate_m7.add_argument("--jagua-binary", type=Path, required=True)
    calibrate_m7.add_argument("--collision-differential", type=Path, required=True)
    calibrate_m7.add_argument("--feasibility", type=Path, required=True)
    calibrate_m7.add_argument("--output", type=Path, required=True)
    calibrate_m7.set_defaults(handler=_calibrate_m7_baseline)

    evaluate_m7 = benchmark_commands.add_parser(
        "m7-evaluate",
        help="execute the frozen M7 policy twice on all evaluation streams",
    )
    evaluate_m7.add_argument("--m0", type=Path, required=True)
    evaluate_m7.add_argument("--frozen-baseline", type=Path, required=True)
    evaluate_m7.add_argument(
        "--archive-root",
        type=Path,
        action="append",
        required=True,
        help="candidate archive root; repeat for isolated M2 runtime roots",
    )
    evaluate_m7.add_argument("--jagua-binary", type=Path, required=True)
    evaluate_m7.add_argument("--output", type=Path, required=True)
    evaluate_m7.set_defaults(handler=_evaluate_m7_baseline)

    prove_m8 = benchmark_commands.add_parser(
        "m8-sparse-proof",
        help="run the six-cell calibration-only certificate exact M8 go/no-go",
    )
    prove_m8.add_argument("--m0", type=Path, required=True)
    prove_m8.add_argument("--frozen-baseline", type=Path, required=True)
    prove_m8.add_argument(
        "--archive-root",
        type=Path,
        action="append",
        required=True,
        help="candidate archive root; repeat for isolated M2 runtime roots",
    )
    prove_m8.add_argument("--jagua-binary", type=Path, required=True)
    prove_m8.add_argument("--output", type=Path, required=True)
    prove_m8.set_defaults(handler=_prove_m8_sparse_oracle)

    portable_m8 = benchmark_commands.add_parser(
        "m8-portable-gate3",
        help="run the frozen two-probe calibration-only portable-fact Gate-3 pipeline",
    )
    portable_m8.add_argument("--m0", type=Path, required=True)
    portable_m8.add_argument("--frozen-baseline", type=Path, required=True)
    portable_m8.add_argument(
        "--archive-root",
        type=Path,
        action="append",
        required=True,
        help="candidate archive root; repeat for isolated M2 runtime roots",
    )
    portable_m8.add_argument("--jagua-binary", type=Path, required=True)
    portable_m8.add_argument("--output", type=Path, required=True)
    portable_m8.set_defaults(handler=_run_m8_portable_fact_gate3)

    profile_m8 = benchmark_commands.add_parser(
        "m8-certificate-profile",
        help="profile one explicit calibration-only M8 certificate prefix",
    )
    profile_m8.add_argument("--m0", type=Path, required=True)
    profile_m8.add_argument("--frozen-baseline", type=Path, required=True)
    profile_m8.add_argument(
        "--archive-root",
        type=Path,
        action="append",
        required=True,
        help="candidate archive root; repeat for isolated M2 runtime roots",
    )
    profile_m8.add_argument("--jagua-binary", type=Path, required=True)
    profile_m8.add_argument("--regime", type=TemporalRegime, required=True)
    profile_m8.add_argument("--seed", type=int, required=True)
    profile_m8.add_argument("--event-count", type=int, required=True)
    profile_m8.add_argument("--output", type=Path, required=True)
    profile_m8.set_defaults(handler=_profile_m8_certificate)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = cast(Callable[[argparse.Namespace], int], args.handler)
    return handler(args)
