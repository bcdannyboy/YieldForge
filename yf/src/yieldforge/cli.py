"""Command-line entry points for the YieldForge experiment loop."""

import argparse
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from yieldforge.archive import CandidateArchive
from yieldforge.datasets.fetch import fetch_file
from yieldforge.datasets.lectra_audit import LectraAuditReport
from yieldforge.datasets.source_manifest import DatasetSourceManifest
from yieldforge.domain import SpyrrowRunConfig, StripPackingProblem
from yieldforge.spyrrow_adapter import SpyrrowAdapter


class DatasetAuditCheckError(ValueError):
    """Raised when passive audit evidence does not match its pinned source."""


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value}")


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _read_strict_json(path: Path, *, label: str) -> object:
    try:
        serialized = path.read_text(encoding="utf-8")
        return json.loads(
            serialized,
            parse_constant=_reject_nonfinite_json,
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise DatasetAuditCheckError(f"Invalid {label} {path}: {error}") from error


def _load_audit_report(path: Path) -> LectraAuditReport:
    payload = _read_strict_json(path, label="Lectra audit report")
    try:
        return LectraAuditReport.model_validate(payload)
    except ValidationError as error:
        raise DatasetAuditCheckError(f"Invalid Lectra audit report {path}: {error}") from error


def _load_audit_manifest(path: Path) -> DatasetSourceManifest:
    payload = _read_strict_json(path, label="dataset source manifest")
    try:
        return DatasetSourceManifest.model_validate(payload)
    except ValidationError as error:
        raise DatasetAuditCheckError(f"Invalid dataset source manifest {path}: {error}") from error


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
    report = _load_audit_report(args.report)
    manifest = _load_audit_manifest(args.manifest)
    if report.dataset_id != manifest.dataset_id:
        raise DatasetAuditCheckError(
            "Lectra audit dataset identity mismatch: "
            f"report={report.dataset_id!r}, manifest={manifest.dataset_id!r}"
        )

    expected_checksums = {source.name: source.checksum for source in manifest.files}
    if report.source_checksums != expected_checksums:
        raise DatasetAuditCheckError(
            "Lectra audit source checksum mismatch: "
            f"report={report.source_checksums!r}, manifest={expected_checksums!r}"
        )

    rows = report.table_rows
    print(
        f"Validated {report.dataset_id} audit: "
        f"tasks={rows['tasks']}, parts={rows['parts']}, "
        f"shapes={rows['shapes']}, constraints={rows['constraints']}"
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = cast(Callable[[argparse.Namespace], int], args.handler)
    return handler(args)
