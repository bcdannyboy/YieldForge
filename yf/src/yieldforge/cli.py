"""Command-line entry points for the YieldForge experiment loop."""

import argparse
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from yieldforge.archive import CandidateArchive
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
from yieldforge.experiments.residual_geometry import (
    evaluate_m3_residual_geometry,
    load_m3_input_pack,
    prepare_m3_input_pack,
    publish_m3_input_pack,
    publish_m3_result,
)
from yieldforge.spyrrow_adapter import SpyrrowAdapter

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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = cast(Callable[[argparse.Namespace], int], args.handler)
    return handler(args)
