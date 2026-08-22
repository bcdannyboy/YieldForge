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
    result = import_catalog(
        database_url=args.database_url,
        catalog_path=args.catalog,
        catalog_manifest_path=args.catalog_manifest,
        source_manifest_path=args.source_manifest,
        audit_report_path=args.audit_report,
    )
    print(
        f"Imported {result.task_count} catalog tasks with artifact SHA-256 {result.catalog_sha256}"
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
    catalog_import.add_argument("--catalog-manifest", type=Path, required=True)
    catalog_import.add_argument("--source-manifest", type=Path, required=True)
    catalog_import.add_argument("--audit-report", type=Path, required=True)
    catalog_import.add_argument("--database-url", required=True)
    catalog_import.set_defaults(handler=_import_dataset_catalog)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = cast(Callable[[argparse.Namespace], int], args.handler)
    return handler(args)
