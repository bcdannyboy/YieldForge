"""Canonical M5 deterministic replay preparation, execution, and evidence I/O."""

from __future__ import annotations

import json
import os
import secrets
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import shapely
from pydantic import BaseModel, ValidationError

from yieldforge.domain import Part
from yieldforge.experiments.contracts import M0ExperimentContract
from yieldforge.experiments.remnant_reuse import (
    M4ReuseInputPack,
    M4ReuseResult,
    validate_m4_result_evidence,
)
from yieldforge.replay.contracts import (
    ReplayEngineIdentity,
    ReplayInput,
    ReplayPolicyIdentity,
    ReplayRateManifest,
    ReplayResult,
    StandardSheetSpec,
    build_replay_input,
)
from yieldforge.replay.engine import run_replay
from yieldforge.residuals.contracts import rule_set_from_m0
from yieldforge.reuse.contracts import FitSearchConfig, RemnantFitConfig

_MAX_M5_ARTIFACT_BYTES = 4 * 1024 * 1024
REGISTERED_M5_SEARCH_CONFIG = FitSearchConfig(
    grid_columns=5,
    grid_rows=5,
    maximum_candidates=512,
)


class M5EvidenceError(ValueError):
    """Canonical M0/M4/M5 evidence failed a bounded validation check."""


def _strict_sources(
    m4_pack: M4ReuseInputPack,
    m4_result: M4ReuseResult,
    m0: M0ExperimentContract,
) -> tuple[M4ReuseInputPack, M4ReuseResult, M0ExperimentContract]:
    try:
        pack = M4ReuseInputPack.model_validate_json(
            json.dumps(m4_pack.model_dump(mode="json"), allow_nan=False), strict=True
        )
        result = M4ReuseResult.model_validate_json(
            json.dumps(m4_result.model_dump(mode="json"), allow_nan=False), strict=True
        )
        contract = M0ExperimentContract.model_validate_json(
            json.dumps(m0.model_dump(mode="json"), allow_nan=False), strict=True
        )
    except (ValidationError, ValueError) as error:
        raise M5EvidenceError("M0 or M4 source evidence is invalid") from error
    if (
        pack.m0_contract_id != contract.contract_id
        or pack.m0_contract_sha256 != contract.content_sha256
        or result.input_id != pack.input_id
        or result.input_sha256 != pack.content_sha256
        or result.m0_contract_id != contract.contract_id
        or result.m0_contract_sha256 != contract.content_sha256
        or pack.shapely_version != shapely.__version__
        or result.shapely_version != shapely.__version__
    ):
        raise M5EvidenceError("M4 evidence does not bind the supplied M0 contract and runtime")
    try:
        validated_result = validate_m4_result_evidence(result, pack=pack, m0=contract)
    except ValueError as error:
        raise M5EvidenceError("M4 evidence failed independent exact revalidation") from error
    return pack, validated_result, contract


def prepare_m5_replay_input(
    m4_pack: M4ReuseInputPack,
    m4_result: M4ReuseResult,
    m0: M0ExperimentContract,
) -> ReplayInput:
    """Build the registered generated two-order replay bound to canonical M0 and M4."""

    pack, result, contract = _strict_sources(m4_pack, m4_result, m0)
    material = pack.assumed_material
    starts_at = datetime(2026, 1, 1, tzinfo=UTC)
    return build_replay_input(
        m0_contract_id=contract.contract_id,
        m0_contract_sha256=contract.content_sha256,
        m4_input_id=pack.input_id,
        m4_input_sha256=pack.content_sha256,
        m4_result_id=result.result_id,
        m4_result_sha256=result.content_sha256,
        engine=ReplayEngineIdentity(shapely_version=shapely.__version__),
        policy=ReplayPolicyIdentity(),
        fit_config=RemnantFitConfig(),
        search_config=REGISTERED_M5_SEARCH_CONFIG,
        rates=ReplayRateManifest(
            purchase_cost_per_area=1.0,
            storage_cost_per_area_hour=0.01,
            return_handling_cost_per_remnant=2.0,
            retrieval_handling_cost_per_remnant=3.0,
            scrap_credit_per_area=0.1,
        ),
        standard_sheet=StandardSheetSpec(
            stock_code="m5-generated-standard-sheet",
            length=10.0,
            height=10.0,
            material=material,
        ),
        order_specs=(
            (
                "m5-generated-order-a",
                starts_at,
                Part(
                    id="m5-generated-part-a",
                    shape=[(0.0, 0.0), (4.0, 0.0), (4.0, 10.0), (0.0, 10.0)],
                    demand=1,
                    allowed_orientations=[0.0],
                ),
                material,
            ),
            (
                "m5-generated-order-b",
                starts_at + timedelta(hours=1),
                Part(
                    id="m5-generated-part-b",
                    shape=[(0.0, 0.0), (3.0, 0.0), (3.0, 10.0), (0.0, 10.0)],
                    demand=1,
                    allowed_orientations=[0.0],
                ),
                material,
            ),
        ),
        horizon_end=starts_at + timedelta(hours=2),
    )


def evaluate_m5_replay(
    replay_input: ReplayInput,
    m0: M0ExperimentContract,
) -> ReplayResult:
    """Validate the M0/runtime binding and execute one pure deterministic replay."""

    try:
        validated_input = ReplayInput.model_validate_json(
            json.dumps(replay_input.model_dump(mode="json"), allow_nan=False), strict=True
        )
        contract = M0ExperimentContract.model_validate_json(
            json.dumps(m0.model_dump(mode="json"), allow_nan=False), strict=True
        )
    except (ValidationError, ValueError) as error:
        raise M5EvidenceError("M5 replay input or M0 contract validation failed") from error
    if (
        validated_input.m0_contract_id != contract.contract_id
        or validated_input.m0_contract_sha256 != contract.content_sha256
    ):
        raise M5EvidenceError("M5 replay input does not bind the supplied M0 contract")
    if validated_input.engine.shapely_version != shapely.__version__:
        raise M5EvidenceError("M5 replay engine does not bind the current Shapely runtime")
    return run_replay(validated_input, rule_set_from_m0(contract.remnant_eligibility))


def _canonical_model_bytes(value: BaseModel) -> bytes:
    return (
        json.dumps(
            value.model_dump(mode="json"),
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()


def _read_regular_file(path: Path) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise M5EvidenceError("M5 artifact could not be inspected") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise M5EvidenceError("M5 artifact must be a regular file and not a symlink")
    if metadata.st_size > _MAX_M5_ARTIFACT_BYTES:
        raise M5EvidenceError("M5 artifact exceeds its byte limit")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            data = stream.read(_MAX_M5_ARTIFACT_BYTES + 1)
    except OSError as error:
        raise M5EvidenceError("M5 artifact could not be read safely") from error
    if len(data) > _MAX_M5_ARTIFACT_BYTES:
        raise M5EvidenceError("M5 artifact exceeds its byte limit")
    return data


def _publish(output_directory: Path, filename: str, value: BaseModel) -> Path:
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    path = output / filename
    data = _canonical_model_bytes(value)
    if path.exists():
        if _read_regular_file(path) != data:
            raise M5EvidenceError("M5 artifact is immutable and differs from existing bytes")
        return path
    temporary = path.with_name(f".{path.name}.tmp-{secrets.token_hex(8)}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        temporary.rename(path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def publish_m5_replay_input(output_directory: Path, replay_input: ReplayInput) -> Path:
    """Publish or verify one immutable canonical M5 replay input."""

    return _publish(
        output_directory,
        f"deterministic-replay-input-{replay_input.input_id}.json",
        replay_input,
    )


def publish_m5_replay_result(output_directory: Path, result: ReplayResult) -> Path:
    """Publish or verify one immutable canonical M5 replay result."""

    return _publish(
        output_directory,
        f"deterministic-replay-result-{result.result_id}.json",
        result,
    )


def load_m5_replay_input(
    path: Path,
    *,
    m4_pack: M4ReuseInputPack,
    m4_result: M4ReuseResult,
    m0: M0ExperimentContract,
) -> ReplayInput:
    """Load canonical input and independently reconstruct the registered fixture."""

    replay_input = load_m5_replay_input_unbound(path)
    expected = prepare_m5_replay_input(m4_pack, m4_result, m0)
    if replay_input != expected:
        raise M5EvidenceError("M5 replay input does not match the registered reconstruction")
    return replay_input


def load_m5_replay_input_unbound(path: Path) -> ReplayInput:
    """Load strict canonical M5 input while preserving its persisted upstream bindings."""

    data = _read_regular_file(Path(path))
    try:
        replay_input = ReplayInput.model_validate_json(data, strict=True)
    except ValidationError as error:
        raise M5EvidenceError("M5 replay input artifact validation failed") from error
    if _canonical_model_bytes(replay_input) != data:
        raise M5EvidenceError("M5 replay input artifact does not use canonical JSON encoding")
    return replay_input


def load_m5_replay_result(
    path: Path,
    *,
    replay_input: ReplayInput,
    m0: M0ExperimentContract,
) -> ReplayResult:
    """Load canonical output and independently replay every transition and cost."""

    data = _read_regular_file(Path(path))
    try:
        result = ReplayResult.model_validate_json(data, strict=True)
    except ValidationError as error:
        raise M5EvidenceError("M5 replay result artifact validation failed") from error
    if _canonical_model_bytes(result) != data:
        raise M5EvidenceError("M5 replay result artifact does not use canonical JSON encoding")
    expected = evaluate_m5_replay(replay_input, m0)
    if result != expected:
        raise M5EvidenceError("M5 replay result does not match independent deterministic replay")
    return result
