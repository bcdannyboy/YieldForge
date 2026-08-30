"""Publish the minimum M10 investment verdict from frozen parent evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import stat
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import NoReturn

from pydantic import ValidationError

from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.experiments.m10_verdict import (
    M10EvidenceSnapshot,
    M10MinimumInvestmentVerdict,
    M10ParentBinding,
    build_minimum_investment_verdict,
)
from yieldforge.oracle.artifact_publisher import (
    M8ArtifactPublicationError,
    publish_immutable_artifact,
)

MAX_PARENT_BYTES = 4 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024
_ARTIFACT_PREFIX = "m10-minimum-investment-verdict"


class M10RunnerError(ValueError):
    """The frozen M10 evidence or two-pass publication contract did not reconcile."""


@dataclass(frozen=True, slots=True)
class M10ParentSpec:
    """One immutable parent location and its expected semantic and raw identities."""

    role: str
    repository_path: str
    schema_version: str
    semantic_id_field: str
    semantic_id: str
    content_sha256: str
    raw_file_sha256: str


M10_PARENT_SPECS: tuple[M10ParentSpec, ...] = (
    M10ParentSpec(
        role="m0_contract",
        repository_path="experiments/m0-contract-v1.json",
        schema_version="yieldforge.m0-contract.v1",
        semantic_id_field="contract_id",
        semantic_id="yfm0-29b7efe8ac2a0a9995c4f907",
        content_sha256=(
            "sha256:29b7efe8ac2a0a9995c4f907a56d7ce0cb9b61217b167f0737f6973c648b9a5f"
        ),
        raw_file_sha256=(
            "sha256:8ad20ca2ffaa4873588a4829d0d4fccfc85269429c6e2363b59d29be150d1c99"
        ),
    ),
    M10ParentSpec(
        role="m6_contract",
        repository_path="benchmarks/temporal/m6-contract-v1.json",
        schema_version="yieldforge.temporal-benchmark-contract.v1",
        semantic_id_field="contract_id",
        semantic_id="yfm6-3eeda3f4feb80813807c501a",
        content_sha256=(
            "sha256:3eeda3f4feb80813807c501ae71299a2add07ed76b75009e2f744daddae5a8aa"
        ),
        raw_file_sha256=(
            "sha256:461220a0a0860a7d31df48f50c3c5f8034a0c5fb5896ab40ae72bd74578bdd35"
        ),
    ),
    M10ParentSpec(
        role="m6_population",
        repository_path="benchmarks/temporal/m6-population-v1.json",
        schema_version="yieldforge.temporal-population.v1",
        semantic_id_field="population_id",
        semantic_id="yftp-49bd7ce5fd34b2779440c52f",
        content_sha256=(
            "sha256:49bd7ce5fd34b2779440c52fabdf2acb8ef80f39b025cdf5a9c6f8a1d2c958f9"
        ),
        raw_file_sha256=(
            "sha256:f864f10b6d3dced65d0ba6acd2c7201b3b01d06865fec748b028bbc4802a1e5b"
        ),
    ),
    M10ParentSpec(
        role="m7_evaluation",
        repository_path=(
            "experiments/results/"
            "m7-evaluation-yfm7eval-f2cb310c4b7e879d119e8f94.json"
        ),
        schema_version="yieldforge.m7-evaluation-result.v1",
        semantic_id_field="result_id",
        semantic_id="yfm7eval-f2cb310c4b7e879d119e8f94",
        content_sha256=(
            "sha256:f2cb310c4b7e879d119e8f940d5a3dc88cd4b26d48087b46323b7be848144931"
        ),
        raw_file_sha256=(
            "sha256:ba8fdbeaddb2ec9c289ead27627fca6a59f83012cd0093f557848b35c710b91f"
        ),
    ),
    M10ParentSpec(
        role="m8_gate3",
        repository_path=(
            "experiments/results/"
            "m8-gate3-decision-yfm8g3decision-c13ec320e9fcd02873bf649c.json"
        ),
        schema_version="yieldforge.m8-gate3-decision.v2",
        semantic_id_field="decision_id",
        semantic_id="yfm8g3decision-c13ec320e9fcd02873bf649c",
        content_sha256=(
            "sha256:c13ec320e9fcd02873bf649c4f8d84a66c48fb5c4a8e67ebf2fb2f5de268b03c"
        ),
        raw_file_sha256=(
            "sha256:8e1ca24321b5fd15445e06ebd680225ee42e10d895207cfbb496544d1613b551"
        ),
    ),
    M10ParentSpec(
        role="m9_repair",
        repository_path=(
            "experiments/results/"
            "m9-two-ply-repair-validation-yfm9r-db0829451b1b0393f2d22559.json"
        ),
        schema_version="yieldforge.m9-two-ply-repair-validation.v1",
        semantic_id_field="result_id",
        semantic_id="yfm9r-db0829451b1b0393f2d22559",
        content_sha256=(
            "sha256:db0829451b1b0393f2d2255990ade1ce783b27a8527f73f3c7bf07e6716438ba"
        ),
        raw_file_sha256=(
            "sha256:16444e2e0f1a6fa5fb57398b290a7b0b66fda271997a48202a403c499060b858"
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class _EvidencePass:
    evidence: M10EvidenceSnapshot
    parent_raw_bytes: tuple[bytes, ...]


@dataclass(frozen=True, slots=True)
class M10RunOutcome:
    artifact_path: Path
    result_id: str
    content_sha256: str
    investment_verdict: str
    productization_decision: str
    formal_economic_band: str
    evidence_pass_wall_seconds: tuple[float, float]


def _canonical_compact_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _canonical_pretty_bytes(value: object) -> bytes:
    return (
        json.dumps(value, allow_nan=False, indent=2, sort_keys=True) + "\n"
    ).encode()


def _repository_parts(repository_path: str) -> tuple[str, ...]:
    if type(repository_path) is not str or not repository_path:
        raise M10RunnerError("M10 parent repository path must be a nonempty string")
    parsed = PurePosixPath(repository_path)
    parts = parsed.parts
    if (
        parsed.is_absolute()
        or not parts
        or repository_path != parsed.as_posix()
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise M10RunnerError("M10 parent repository path must remain inside evidence root")
    return parts


def _directory_flags() -> int:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return flags


def _open_absolute_directory_no_follow(path: Path, *, label: str) -> int:
    absolute = Path(os.path.abspath(os.fspath(path)))
    flags = _directory_flags()
    try:
        descriptor = os.open(os.path.sep, flags)
    except OSError as error:  # pragma: no cover - a broken host filesystem
        raise M10RunnerError(f"{label} directory could not be opened safely") from error
    try:
        for component in absolute.parts[1:]:
            try:
                child = os.open(component, flags, dir_fd=descriptor)
            except OSError as error:
                raise M10RunnerError(
                    f"{label} directory must not contain symlinks"
                ) from error
            os.close(descriptor)
            descriptor = child
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise M10RunnerError(f"{label} directory is not a directory")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read_regular_file_no_follow(
    evidence_root: Path,
    repository_path: str,
    *,
    max_bytes: int,
) -> bytes:
    """Read one bounded regular file without following any path component."""

    if type(max_bytes) is not int or max_bytes <= 0:
        raise M10RunnerError("M10 parent size limit must be a positive integer")
    parts = _repository_parts(repository_path)
    directory_descriptor = _open_absolute_directory_no_follow(
        Path(evidence_root),
        label="M10 evidence root",
    )
    file_descriptor: int | None = None
    try:
        for component in parts[:-1]:
            try:
                child = os.open(
                    component,
                    _directory_flags(),
                    dir_fd=directory_descriptor,
                )
            except OSError as error:
                raise M10RunnerError(
                    "M10 parent repository directory must not contain symlinks"
                ) from error
            os.close(directory_descriptor)
            directory_descriptor = child

        name = parts[-1]
        try:
            named = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
        except OSError as error:
            raise M10RunnerError("M10 parent regular file is unavailable") from error
        if not stat.S_ISREG(named.st_mode):
            raise M10RunnerError("M10 parent must be a regular file")
        if named.st_size > max_bytes:
            raise M10RunnerError("M10 parent exceeds the registered size limit")

        file_flags = os.O_RDONLY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            file_flags |= os.O_NOFOLLOW
        if hasattr(os, "O_NONBLOCK"):
            file_flags |= os.O_NONBLOCK
        try:
            file_descriptor = os.open(
                name,
                file_flags,
                dir_fd=directory_descriptor,
            )
        except OSError as error:
            raise M10RunnerError("M10 parent must remain a regular file") from error
        opened = os.fstat(file_descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise M10RunnerError("M10 parent must remain a regular file")
        if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
            raise M10RunnerError("M10 parent identity changed before reading")
        if opened.st_size > max_bytes:
            raise M10RunnerError("M10 parent exceeds the registered size limit")

        chunks: list[bytes] = []
        total = 0
        while total <= max_bytes:
            chunk = os.read(
                file_descriptor,
                min(_READ_CHUNK_BYTES, max_bytes + 1 - total),
            )
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > max_bytes:
            raise M10RunnerError("M10 parent exceeds the registered size limit")
        raw = b"".join(chunks)
        final = os.fstat(file_descriptor)
        before = (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        )
        after = (
            final.st_dev,
            final.st_ino,
            final.st_size,
            final.st_mtime_ns,
            final.st_ctime_ns,
        )
        if before != after or len(raw) != final.st_size:
            raise M10RunnerError("M10 parent changed during bounded read")
        return raw
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        os.close(directory_descriptor)


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise M10RunnerError(f"M10 JSON contains duplicate key {key!r}")
        result[key] = value
    return result


def _reject_nonfinite_constant(value: str) -> NoReturn:
    raise M10RunnerError(f"M10 JSON contains non-finite value {value}")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise M10RunnerError("M10 JSON contains a non-finite number")
    return parsed


def _parse_canonical_json(raw: bytes, *, label: str) -> dict[str, object]:
    """Strict-load one finite, duplicate-free, canonical JSON mapping."""

    if type(raw) is not bytes:
        raise M10RunnerError(f"{label} JSON input must be exact bytes")
    try:
        text = raw.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_nonfinite_constant,
            parse_float=_finite_float,
        )
    except M10RunnerError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, OverflowError, ValueError) as error:
        raise M10RunnerError(f"{label} is not strict finite JSON") from error
    if type(value) is not dict:
        raise M10RunnerError(f"{label} JSON root must be an object")
    try:
        canonical = _canonical_pretty_bytes(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise M10RunnerError(f"{label} JSON is not canonically serializable") from error
    if raw != canonical:
        raise M10RunnerError(f"{label} parent bytes are not canonical pretty JSON")
    return value


def _validate_parent_semantics(
    spec: M10ParentSpec,
    payload: dict[str, object],
) -> None:
    if payload.get("schema_version") != spec.schema_version:
        raise M10RunnerError(f"M10 {spec.role} schema version differs")
    if payload.get(spec.semantic_id_field) != spec.semantic_id:
        raise M10RunnerError(f"M10 {spec.role} semantic ID differs")
    if payload.get("content_sha256") != spec.content_sha256:
        raise M10RunnerError(f"M10 {spec.role} content SHA-256 differs")
    digest = semantic_sha256(
        payload,
        excluded_fields={spec.semantic_id_field, "content_sha256"},
    )
    if spec.content_sha256 != f"sha256:{digest}":
        raise M10RunnerError(f"M10 {spec.role} semantic content does not reconcile")
    if not spec.semantic_id.endswith(digest[:24]):
        raise M10RunnerError(f"M10 {spec.role} semantic ID/hash do not reconcile")


def _load_parent(
    evidence_root: Path,
    spec: M10ParentSpec,
) -> tuple[M10ParentBinding, dict[str, object], bytes]:
    raw = _read_regular_file_no_follow(
        evidence_root,
        spec.repository_path,
        max_bytes=MAX_PARENT_BYTES,
    )
    raw_file_sha256 = f"sha256:{hashlib.sha256(raw).hexdigest()}"
    if raw_file_sha256 != spec.raw_file_sha256:
        raise M10RunnerError(f"M10 {spec.role} raw SHA-256 differs")
    payload = _parse_canonical_json(raw, label=f"M10 {spec.role}")
    _validate_parent_semantics(spec, payload)
    binding = M10ParentBinding(
        role=spec.role,
        repository_path=spec.repository_path,
        schema_version=spec.schema_version,
        semantic_id=spec.semantic_id,
        content_sha256=spec.content_sha256,
        raw_file_sha256=raw_file_sha256,
    )
    return binding, payload, raw


def _mapping(value: object, *, label: str) -> dict[str, object]:
    if type(value) is not dict:
        raise M10RunnerError(f"{label} must be an object")
    return value


def _integer(value: object, *, expected: int, label: str) -> int:
    if type(value) is not int or value != expected:
        raise M10RunnerError(f"{label} must equal {expected}")
    return value


def _provenance_kind(m6_contract: dict[str, object], *, field: str) -> str:
    entries = m6_contract.get("field_provenance")
    if type(entries) is not list:
        raise M10RunnerError("M10 M6 field provenance must be a list")
    matching: list[str] = []
    for entry in entries:
        item = _mapping(entry, label="M10 M6 provenance entry")
        if item.get("field") == field:
            kind = item.get("kind")
            if type(kind) is not str:
                raise M10RunnerError(f"M10 M6 {field} provenance kind must be a string")
            matching.append(kind)
    if len(matching) != 1:
        raise M10RunnerError(f"M10 M6 {field} provenance must occur exactly once")
    return matching[0]


def _extract_evidence(
    bindings: tuple[M10ParentBinding, ...],
    payloads: tuple[dict[str, object], ...],
) -> M10EvidenceSnapshot:
    parents = {
        spec.role: payload
        for spec, payload in zip(M10_PARENT_SPECS, payloads, strict=True)
    }
    m0 = parents["m0_contract"]
    m6_contract = parents["m6_contract"]
    m6_population = parents["m6_population"]
    m7 = parents["m7_evaluation"]
    m8 = parents["m8_gate3"]
    m9 = parents["m9_repair"]

    supporting = _mapping(
        _mapping(m0.get("decision_gates"), label="M10 M0 decision gates").get(
            "supporting"
        ),
        label="M10 M0 supporting gates",
    )
    required_corpora = _integer(
        supporting.get("minimum_geometry_corpora_with_positive_evidence"),
        expected=2,
        label="M10 M0 required positive geometry corpus count",
    )
    source_catalog = _mapping(
        m6_contract.get("source_catalog"),
        label="M10 M6 source catalog",
    )
    dataset_id = source_catalog.get("dataset_id")
    if type(dataset_id) is not str or not dataset_id:
        raise M10RunnerError("M10 M6 source dataset ID must be a nonempty string")

    m0_spec = M10_PARENT_SPECS[0]
    m6_spec = M10_PARENT_SPECS[1]
    if (
        m6_contract.get("m0_contract_id") != m0_spec.semantic_id
        or m6_contract.get("m0_contract_sha256") != m0_spec.content_sha256
    ):
        raise M10RunnerError("M10 M6-to-M0 parent binding differs")
    if (
        m6_population.get("contract_id") != m6_spec.semantic_id
        or m6_population.get("contract_sha256") != m6_spec.content_sha256
        or m6_population.get("source_catalog_sha256")
        != source_catalog.get("artifact_sha256")
    ):
        raise M10RunnerError("M10 M6 population parent binding differs")
    if (
        m7.get("m0_contract_id") != m0_spec.semantic_id
        or m7.get("m0_contract_sha256") != m0_spec.content_sha256
    ):
        raise M10RunnerError("M10 M7-to-M0 parent binding differs")

    _integer(m7.get("stream_count"), expected=36, label="M10 M7 stream count")
    _integer(m7.get("repeat_count"), expected=2, label="M10 M7 repeat count")
    if m7.get("repeat_content_identity_match") is not True:
        raise M10RunnerError("M10 M7 repeat content identity must match")
    if m7.get("evaluation_partition_opened") is not True:
        raise M10RunnerError("M10 M7 baseline evaluation must be complete")

    if m8.get("decision") != "hold_performance":
        raise M10RunnerError("M10 M8 decision must remain hold_performance")
    if m8.get("evaluation_opened") is not False:
        raise M10RunnerError("M10 M8 oracle evaluation must remain unopened")
    if m8.get("official_six_cell_executed") is not False:
        raise M10RunnerError("M10 M8 official six-cell run must remain unexecuted")
    for absent_metric in (
        "oracle_savings_percent",
        "unknown_future_contribution_percentage_points",
    ):
        if absent_metric in m8:
            raise M10RunnerError(f"M10 M8 unexpectedly supplies {absent_metric}")

    evaluator_result = _mapping(
        m9.get("evaluator_result"),
        label="M10 M9 evaluator result",
    )
    if evaluator_result.get("decision") != "pass_decision_feasibility":
        raise M10RunnerError("M10 M9 decision must remain pass_decision_feasibility")

    return M10EvidenceSnapshot.model_validate(
        {
            "parents": bindings,
            "geometry_corpus_ids": (dataset_id,),
            "required_positive_geometry_corpus_count": required_corpora,
            "chronology_provenance": _provenance_kind(
                m6_contract,
                field="chronology",
            ),
            "economics_provenance": _provenance_kind(
                m6_contract,
                field="economics",
            ),
            "material_provenance": _provenance_kind(
                m6_contract,
                field="material",
            ),
            "baseline_stream_count": m7["stream_count"],
            "baseline_repeat_count": m7["repeat_count"],
            "baseline_repeat_identity_match": m7["repeat_content_identity_match"],
            "m8_decision": m8["decision"],
            "oracle_evaluation_opened": m8["evaluation_opened"],
            "oracle_savings_percent": None,
            "unknown_future_contribution_percentage_points": None,
            "m9_decision": evaluator_result["decision"],
        },
        strict=True,
    )


def _load_evidence_pass(evidence_root: Path) -> _EvidencePass:
    loaded = tuple(_load_parent(Path(evidence_root), spec) for spec in M10_PARENT_SPECS)
    bindings = tuple(item[0] for item in loaded)
    payloads = tuple(item[1] for item in loaded)
    raw_bytes = tuple(item[2] for item in loaded)
    return _EvidencePass(
        evidence=_extract_evidence(bindings, payloads),
        parent_raw_bytes=raw_bytes,
    )


def _validate_artifact_bytes(data: bytes) -> bytes:
    try:
        payload = _parse_canonical_json(data, label="M10 verdict artifact")
        verdict = M10MinimumInvestmentVerdict.model_validate_json(data, strict=True)
    except (M10RunnerError, ValidationError) as error:
        if isinstance(error, M10RunnerError):
            raise
        raise M10RunnerError("M10 verdict artifact failed strict validation") from error
    canonical = _canonical_pretty_bytes(verdict.model_dump(mode="json"))
    if canonical != data or payload != verdict.model_dump(mode="json"):
        raise M10RunnerError("M10 verdict artifact is not canonical")
    return canonical


def _build_pass(
    evidence_root: Path,
) -> tuple[_EvidencePass, M10MinimumInvestmentVerdict, float]:
    started = time.perf_counter()
    loaded = _load_evidence_pass(evidence_root)
    verdict = build_minimum_investment_verdict(loaded.evidence)
    elapsed = time.perf_counter() - started
    if not math.isfinite(elapsed) or elapsed < 0.0:
        raise M10RunnerError("M10 evidence pass produced an invalid wall time")
    return loaded, verdict, elapsed


def run_m10_minimum_verdict(
    *,
    evidence_root: Path,
    output_directory: Path,
) -> M10RunOutcome:
    """Fresh-load and build twice, reconcile, then publish one immutable verdict."""

    first_evidence, first_verdict, first_wall = _build_pass(Path(evidence_root))
    second_evidence, second_verdict, second_wall = _build_pass(Path(evidence_root))
    if first_evidence.parent_raw_bytes != second_evidence.parent_raw_bytes:
        raise M10RunnerError("M10 fresh parent bytes differ between passes")
    first_semantic = _canonical_compact_bytes(first_verdict.model_dump(mode="json"))
    second_semantic = _canonical_compact_bytes(second_verdict.model_dump(mode="json"))
    if first_semantic != second_semantic:
        raise M10RunnerError("M10 fresh semantic results differ between passes")

    artifact_bytes = _canonical_pretty_bytes(first_verdict.model_dump(mode="json"))
    artifact_path = Path(output_directory) / (
        f"{_ARTIFACT_PREFIX}-{first_verdict.result_id}.json"
    )
    try:
        published = publish_immutable_artifact(
            artifact_path,
            artifact_bytes,
            validate=_validate_artifact_bytes,
            label="M10 minimum investment verdict artifact",
        )
    except M8ArtifactPublicationError:
        raise
    return M10RunOutcome(
        artifact_path=published,
        result_id=first_verdict.result_id,
        content_sha256=first_verdict.content_sha256,
        investment_verdict=first_verdict.investment_verdict,
        productization_decision=first_verdict.productization_decision,
        formal_economic_band=first_verdict.formal_economic_band,
        evidence_pass_wall_seconds=(first_wall, second_wall),
    )


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence-root",
        type=Path,
        required=True,
        help="YieldForge root containing the six frozen parent artifacts",
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        required=True,
        help="Directory for the immutable M10 verdict artifact",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        outcome = run_m10_minimum_verdict(
            evidence_root=args.evidence_root,
            output_directory=args.output_directory,
        )
    except (M10RunnerError, M8ArtifactPublicationError, ValidationError) as error:
        raise SystemExit(f"M10 minimum verdict failed: {error}") from error
    print(
        json.dumps(
            {
                "artifact_path": str(outcome.artifact_path),
                "formal_economic_band": outcome.formal_economic_band,
                "investment_verdict": outcome.investment_verdict,
                "productization_decision": outcome.productization_decision,
                "result_id": outcome.result_id,
            },
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "M10_PARENT_SPECS",
    "M10RunOutcome",
    "M10RunnerError",
    "run_m10_minimum_verdict",
]
