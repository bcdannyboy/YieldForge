import json
from pathlib import Path
from typing import Literal

import pytest
from pydantic import StrictInt

from yieldforge.experiments.contracts import (
    ExperimentContractError,
    FrozenExperimentModel,
    canonical_pretty_json_bytes,
    load_frozen_json,
)


class TinyContract(FrozenExperimentModel):
    schema_version: Literal["tiny.v1"] = "tiny.v1"
    value: StrictInt


def test_frozen_json_round_trips_only_canonical_bytes(tmp_path: Path) -> None:
    contract = TinyContract(value=7)
    path = tmp_path / "tiny.json"
    path.write_bytes(canonical_pretty_json_bytes(contract))

    restored = load_frozen_json(path, TinyContract)

    assert restored == contract
    assert canonical_pretty_json_bytes(restored) == path.read_bytes()


def test_frozen_json_rejects_duplicate_keys(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text('{"schema_version":"tiny.v1","value":7,"value":8}\n')

    with pytest.raises(ExperimentContractError, match="duplicate JSON key: value"):
        load_frozen_json(path, TinyContract)


def test_frozen_json_rejects_noncanonical_encoding(tmp_path: Path) -> None:
    path = tmp_path / "compact.json"
    path.write_text('{"schema_version":"tiny.v1","value":7}\n')

    with pytest.raises(ExperimentContractError, match="canonical JSON encoding"):
        load_frozen_json(path, TinyContract)


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": "tiny.v1", "value": 7, "unexpected": True},
        {"schema_version": "wrong.v1", "value": 7},
        {"schema_version": "tiny.v1", "value": 7.0},
    ],
)
def test_frozen_json_rejects_invalid_contracts(tmp_path: Path, payload: dict[str, object]) -> None:
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    with pytest.raises(ExperimentContractError, match="contract validation failed"):
        load_frozen_json(path, TinyContract)


def test_frozen_json_rejects_nonfinite_constants(tmp_path: Path) -> None:
    path = tmp_path / "nan.json"
    path.write_text('{\n  "schema_version": "tiny.v1",\n  "value": NaN\n}\n')

    with pytest.raises(ExperimentContractError, match="nonfinite JSON constant: NaN"):
        load_frozen_json(path, TinyContract)


def test_frozen_json_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_bytes(canonical_pretty_json_bytes(TinyContract(value=7)))
    path = tmp_path / "link.json"
    path.symlink_to(target)

    with pytest.raises(ExperimentContractError, match="regular file and not a symlink"):
        load_frozen_json(path, TinyContract)


def test_frozen_json_rejects_oversized_file(tmp_path: Path) -> None:
    path = tmp_path / "large.json"
    path.write_bytes(b"x" * (1024 * 1024 + 1))

    with pytest.raises(ExperimentContractError, match="exceeds 1048576 bytes"):
        load_frozen_json(path, TinyContract)
