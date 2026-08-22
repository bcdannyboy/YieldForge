"""Strict, content-stable contracts for registered YieldForge experiments."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

_MAX_ARTIFACT_BYTES = 1024 * 1024


class FrozenExperimentModel(BaseModel):
    """Strict immutable base for every registered experiment artifact."""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
    )


class ExperimentContractError(ValueError):
    """A registered experiment artifact failed a fail-closed check."""


def canonical_pretty_json_bytes(value: BaseModel) -> bytes:
    """Return the sole accepted committed encoding for one experiment artifact."""

    return (
        json.dumps(
            value.model_dump(mode="json"),
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ExperimentContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite_constant(value: str) -> None:
    raise ExperimentContractError(f"nonfinite JSON constant: {value}")


def _read_bounded_regular_file(path: Path) -> bytes:
    try:
        file_stat = path.lstat()
    except OSError as error:
        raise ExperimentContractError(f"cannot inspect artifact: {error}") from error
    if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
        raise ExperimentContractError("artifact path must be a regular file and not a symlink")
    if file_stat.st_size > _MAX_ARTIFACT_BYTES:
        raise ExperimentContractError(f"artifact exceeds {_MAX_ARTIFACT_BYTES} bytes")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ExperimentContractError(f"cannot open artifact safely: {error}") from error
    try:
        data = os.read(descriptor, _MAX_ARTIFACT_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(data) > _MAX_ARTIFACT_BYTES:
        raise ExperimentContractError(f"artifact exceeds {_MAX_ARTIFACT_BYTES} bytes")
    return data


def load_frozen_json[ExperimentModel: FrozenExperimentModel](
    path: Path, model: type[ExperimentModel]
) -> ExperimentModel:
    """Load one bounded canonical JSON artifact into a strict frozen model."""

    data = _read_bounded_regular_file(Path(path))
    try:
        payload = json.loads(
            data,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_constant,
        )
    except ExperimentContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExperimentContractError(f"artifact is not valid JSON: {error}") from error
    try:
        result = model.model_validate(payload)
    except ValidationError as error:
        raise ExperimentContractError(f"contract validation failed: {error}") from error
    if data != canonical_pretty_json_bytes(result):
        raise ExperimentContractError("artifact does not use canonical JSON encoding")
    return result
