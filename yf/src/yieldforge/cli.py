"""Command-line entry points for the YieldForge experiment loop."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from yieldforge.archive import CandidateArchive
from yieldforge.domain import SpyrrowRunConfig, StripPackingProblem
from yieldforge.spyrrow_adapter import SpyrrowAdapter


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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if (args.command, args.candidate_command) != ("candidates", "generate"):
        raise AssertionError("unhandled command")

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
