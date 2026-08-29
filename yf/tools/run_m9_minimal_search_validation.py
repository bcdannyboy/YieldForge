#!/usr/bin/env python3
"""Execute and immutably publish the bounded minimal M9 search validation."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import stat
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from pathlib import Path

from tests.oracle.fixtures import exhaustive_certificate_cases
from yieldforge.oracle.search_validation import (
    M9ObjectiveEvaluation,
    M9SearchValidationResult,
    evaluate_search_validation,
)

_SCHEMA_VERSION = "yieldforge.m9-minimal-search-validation.v1"
_FIXTURE_SOURCE = "tests.oracle.fixtures.exhaustive_certificate_cases"
_CLAIM_CEILING = (
    "finite_registered_45_case_decision_validation_only_not_global_policy_optimality_"
    "physical_or_commercial_evidence"
)
_OBJECTIVE_LABELS = ("scrap_only", "zero_total_terminal_credit")


class M9RunnerError(RuntimeError):
    """The repeated M9 evaluation or immutable publication failed closed."""


@dataclass(frozen=True)
class M9RunOutcome:
    """Published identity plus volatile operational timings kept outside the artifact."""

    artifact_path: Path
    result_id: str
    content_sha256: str
    decision: str
    pass_wall_seconds: tuple[float, float]


@dataclass(frozen=True)
class _EvaluationPass:
    ordered_case_ids: tuple[str, ...]
    evaluator_payload: dict[str, object]
    evaluator_semantic_bytes: bytes
    decision: str
    wall_seconds: float


def _json_value(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, Enum):
        return _json_value(value.value)
    if isinstance(value, Mapping):
        converted: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise M9RunnerError("M9 semantic JSON requires exact string mapping keys")
            converted[key] = _json_value(item)
        return converted
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if value is None or type(value) in {str, int, bool}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise M9RunnerError("M9 semantic JSON refuses non-finite floats")
        return value
    raise M9RunnerError(
        f"M9 semantic JSON does not support {type(value).__name__}"
    )


def _canonical_bytes(payload: Mapping[str, object]) -> bytes:
    converted = _json_value(payload)
    if not isinstance(converted, dict):
        raise M9RunnerError("M9 canonical semantic payload must be a mapping")
    return json.dumps(
        converted,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _pretty_bytes(payload: Mapping[str, object]) -> bytes:
    converted = _json_value(payload)
    if not isinstance(converted, dict):
        raise M9RunnerError("M9 published payload must be a mapping")
    return (
        json.dumps(
            converted,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _validate_objective(
    objective: M9ObjectiveEvaluation,
    *,
    expected_label: str,
    ordered_case_ids: tuple[str, ...],
) -> None:
    if objective.objective_label != expected_label:
        raise M9RunnerError("M9 evaluator objective labels do not reconcile")
    record_ids = tuple(record.case_id for record in objective.cases)
    if record_ids != ordered_case_ids:
        raise M9RunnerError("M9 evaluator case IDs do not reconcile with fresh fixtures")
    if any(record.objective_label != expected_label for record in objective.cases):
        raise M9RunnerError("M9 case objective labels do not reconcile")
    if objective.complete is not (
        len(objective.cases) == 45 and all(record.complete for record in objective.cases)
    ):
        raise M9RunnerError("M9 objective completeness does not reconcile")
    every_optimal = all(
        record.selected_action_is_globally_optimal for record in objective.cases
    )
    if objective.every_selected_action_is_globally_optimal is not every_optimal:
        raise M9RunnerError("M9 objective global-optimality aggregate does not reconcile")
    regrets = tuple(record.absolute_first_action_regret for record in objective.cases)
    if any(not math.isfinite(value) or value < 0.0 for value in regrets):
        raise M9RunnerError("M9 objective contains an invalid absolute regret")
    if objective.max_absolute_first_action_regret != max(regrets, default=0.0):
        raise M9RunnerError("M9 objective maximum regret does not reconcile")
    counterexample_ids = tuple(record.case_id for record in objective.counterexamples)
    expected_counterexamples = tuple(
        record.case_id
        for record in objective.cases
        if not record.selected_action_is_globally_optimal
    )
    if counterexample_ids != expected_counterexamples:
        raise M9RunnerError("M9 objective counterexamples do not reconcile")
    expected_conclusion = (
        "pass_decision_feasibility"
        if objective.complete
        and every_optimal
        and objective.max_absolute_first_action_regret == 0.0
        and objective.information_null_controls_pass
        else "fail_search_gap"
    )
    if objective.conclusion != expected_conclusion:
        raise M9RunnerError("M9 objective conclusion does not reconcile")


def _validate_evaluator_result(
    result: M9SearchValidationResult,
    *,
    ordered_case_ids: tuple[str, ...],
) -> None:
    if len(ordered_case_ids) != len(set(ordered_case_ids)) or any(
        not case_id for case_id in ordered_case_ids
    ):
        raise M9RunnerError("M9 fresh fixture case IDs must be nonempty and unique")
    if len(ordered_case_ids) != 45 or result.case_count != len(ordered_case_ids):
        raise M9RunnerError("M9 result must contain exactly 45 registered cases")
    if tuple(result.objective_labels) != _OBJECTIVE_LABELS:
        raise M9RunnerError("M9 evaluator objective census differs")
    _validate_objective(
        result.primary,
        expected_label=_OBJECTIVE_LABELS[0],
        ordered_case_ids=ordered_case_ids,
    )
    _validate_objective(
        result.terminal_sensitivity,
        expected_label=_OBJECTIVE_LABELS[1],
        ordered_case_ids=ordered_case_ids,
    )
    expected_controls = tuple(
        case_id
        for case_id in ordered_case_ids
        if case_id.endswith("zero-no-fit-equal-separated-two")
    )
    if result.information_null_control_case_ids != expected_controls or len(
        expected_controls
    ) != 5:
        raise M9RunnerError("M9 information-null control census does not reconcile")
    no_reversal = result.primary.conclusion == result.terminal_sensitivity.conclusion
    if result.terminal_conclusion_does_not_reverse is not no_reversal:
        raise M9RunnerError("M9 terminal-sensitivity conclusion does not reconcile")
    expected_decision = (
        "pass_decision_feasibility"
        if result.primary.complete
        and result.primary.every_selected_action_is_globally_optimal
        and result.primary.max_absolute_first_action_regret == 0.0
        and result.primary.information_null_controls_pass
        and no_reversal
        else "fail_search_gap"
    )
    if result.decision != expected_decision:
        raise M9RunnerError("M9 aggregate decision does not reconcile")


def _run_evaluation_pass() -> _EvaluationPass:
    started = time.perf_counter()
    cases = exhaustive_certificate_cases()
    ordered_case_ids = tuple(case.case_id for case in cases)
    result = evaluate_search_validation(cases)
    _validate_evaluator_result(result, ordered_case_ids=ordered_case_ids)
    payload = _json_value(result)
    if not isinstance(payload, dict):
        raise M9RunnerError("M9 evaluator result did not serialize to a mapping")
    semantic_bytes = _canonical_bytes(payload)
    elapsed = time.perf_counter() - started
    if not math.isfinite(elapsed) or elapsed < 0.0:
        raise M9RunnerError("M9 evaluation produced an invalid wall time")
    return _EvaluationPass(
        ordered_case_ids=ordered_case_ids,
        evaluator_payload=payload,
        evaluator_semantic_bytes=semantic_bytes,
        decision=result.decision,
        wall_seconds=elapsed,
    )


def _read_regular_destination(directory_fd: int, name: str) -> bytes | None:
    try:
        named_metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(named_metadata.st_mode):
        raise M9RunnerError("M9 destination must be a regular file")
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except OSError as error:
        raise M9RunnerError("M9 destination must remain a regular file") from error
    try:
        opened_metadata = os.fstat(descriptor)
        if not stat.S_ISREG(opened_metadata.st_mode):
            raise M9RunnerError("M9 destination must be a regular file")
        if (opened_metadata.st_dev, opened_metadata.st_ino) != (
            named_metadata.st_dev,
            named_metadata.st_ino,
        ):
            raise M9RunnerError("M9 destination identity changed during readback")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 64 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _publish_immutable(
    *,
    output_directory: Path,
    name: str,
    payload: bytes,
) -> Path:
    output_directory.mkdir(parents=True, exist_ok=True)
    directory_metadata = output_directory.lstat()
    if not stat.S_ISDIR(directory_metadata.st_mode):
        raise M9RunnerError("M9 output directory must be a real directory")
    directory_flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    try:
        directory_fd = os.open(output_directory, directory_flags)
    except OSError as error:
        raise M9RunnerError("M9 output directory must be a real directory") from error
    temporary = f".{name}.{uuid.uuid4().hex}.tmp"
    temporary_created = False
    try:
        existing = _read_regular_destination(directory_fd, name)
        if existing is not None:
            if existing != payload:
                raise M9RunnerError("refusing to replace a different existing artifact")
            return output_directory / name

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(temporary, flags, 0o644, dir_fd=directory_fd)
        temporary_created = True
        try:
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise M9RunnerError("M9 artifact staging made no progress")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

        try:
            os.link(
                temporary,
                name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileExistsError:
            raced = _read_regular_destination(directory_fd, name)
            if raced != payload:
                if raced is None:
                    raise M9RunnerError("M9 destination vanished during publication") from None
                raise M9RunnerError(
                    "refusing to replace a different existing artifact"
                ) from None
        os.unlink(temporary, dir_fd=directory_fd)
        temporary_created = False
        os.fsync(directory_fd)
        published = _read_regular_destination(directory_fd, name)
        if published != payload:
            raise M9RunnerError("M9 published artifact failed immutable readback")
        return output_directory / name
    finally:
        if temporary_created:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        os.close(directory_fd)


def run_minimal_search_validation(*, output_directory: Path) -> M9RunOutcome:
    """Run two fresh exhaustive passes and publish only byte-identical semantics."""

    first = _run_evaluation_pass()
    second = _run_evaluation_pass()
    if first.ordered_case_ids != second.ordered_case_ids:
        raise M9RunnerError("M9 fresh fixture case IDs differ between repeats")
    if first.evaluator_semantic_bytes != second.evaluator_semantic_bytes:
        raise M9RunnerError("M9 repeated semantic results differ")
    if first.decision != second.decision:
        raise M9RunnerError("M9 repeated decisions differ")

    reproducibility_digest = hashlib.sha256(first.evaluator_semantic_bytes).hexdigest()
    semantic_core: dict[str, object] = {
        "schema_version": _SCHEMA_VERSION,
        "fixture_source": _FIXTURE_SOURCE,
        "ordered_case_ids": first.ordered_case_ids,
        "evaluator_result": first.evaluator_payload,
        "fixture_build_count": 2,
        "repeat_count": 2,
        "reproducibility_sha256": f"sha256:{reproducibility_digest}",
        "repeat_semantic_identity_match": True,
        "evaluation_partition_opened": False,
        "claim_ceiling": _CLAIM_CEILING,
    }
    digest = hashlib.sha256(_canonical_bytes(semantic_core)).hexdigest()
    result_id = f"yfm9-{digest[:24]}"
    content_sha256 = f"sha256:{digest}"
    artifact_payload = {
        **semantic_core,
        "result_id": result_id,
        "content_sha256": content_sha256,
    }
    artifact_bytes = _pretty_bytes(artifact_payload)
    artifact_name = f"m9-minimal-search-validation-{result_id}.json"
    artifact_path = _publish_immutable(
        output_directory=Path(output_directory),
        name=artifact_name,
        payload=artifact_bytes,
    )
    return M9RunOutcome(
        artifact_path=artifact_path,
        result_id=result_id,
        content_sha256=content_sha256,
        decision=first.decision,
        pass_wall_seconds=(first.wall_seconds, second.wall_seconds),
    )


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-directory",
        type=Path,
        required=True,
        help="Existing or creatable directory for the immutable M9 result",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        outcome = run_minimal_search_validation(
            output_directory=args.output_directory,
        )
    except M9RunnerError as error:
        print(f"M9 minimal search validation failed closed: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "artifact_path": str(outcome.artifact_path),
                "content_sha256": outcome.content_sha256,
                "decision": outcome.decision,
                "pass_wall_seconds": list(outcome.pass_wall_seconds),
                "result_id": outcome.result_id,
            },
            allow_nan=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
