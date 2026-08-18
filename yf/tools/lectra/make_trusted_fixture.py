"""Create tiny trusted Lectra-shaped pickle files for container smoke tests."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

EXPECTED_FILENAMES = ("constraints.gz", "parts.gz", "shapes.gz", "tasks.gz")


class _AttemptHostOutputWrite:
    """Trusted test probe that attempts code execution when it is deserialized."""

    def __reduce__(self) -> tuple[object, tuple[str]]:
        command = "mkdir -p /output && printf unsafe > /output/pickle-escape"
        return os.system, (command,)


def _trusted_frames(*, adversarial_output_write: bool = False) -> dict[str, Any]:
    import pandas as pd

    tasks = pd.DataFrame(
        [
            {
                "efficiency": 0.5,
                "duration": 1.25,
                "sheet_width": 10.0,
                "sheet_length": 20.0,
                "sheet_type": "sheet",
                "tasks_index": 1,
                "is_train": True,
                "is_val": False,
                "is_test": False,
            }
        ]
    )
    if adversarial_output_write:
        tasks["adversarial_probe"] = [_AttemptHostOutputWrite()]
    parts = pd.DataFrame([{"tasks_index": 1, "parts_id": 10, "shape_hash": "shape-a"}])
    shapes = pd.DataFrame(
        [
            {
                "shape_hash": "shape-a",
                "raw": [0.0, 0.0, 1.0, 0.0, 1.0, 1.0],
                "sizes": [3],
            }
        ]
    )
    constraint_columns = [
        "type",
        "tasks_index",
        "parts_1",
        "parts_2",
        "p1_x",
        "p1_y",
        "p2_x",
        "p2_y",
        "r1_start",
        "r1_end",
        "r1_flip_x",
        "y_min",
        "y_max",
        "x_offset",
        "y_offset",
        "motif_order",
        "x_alignment_type",
        "y_alignment_type",
        "proximity_type",
        "max_distance",
        "groups_relative_orientation",
        "is_frozen",
    ]
    constraint = dict.fromkeys(constraint_columns)
    constraint.update({"type": "opaque-a", "tasks_index": 1, "parts_1": [10], "p1_x": 0.0})
    constraints = pd.DataFrame([constraint], columns=constraint_columns)
    return {"constraints": constraints, "parts": parts, "shapes": shapes, "tasks": tasks}


def _file_identity(path: Path) -> tuple[int, str]:
    payload = path.read_bytes()
    return len(payload), hashlib.md5(payload, usedforsecurity=False).hexdigest()


def write_fixture(
    output_dir: Path,
    manifest_output: Path | None = None,
    *,
    adversarial_output_write: bool = False,
) -> None:
    """Write the trusted frames and optionally their test-only source manifest."""
    if output_dir.exists():
        if not output_dir.is_dir() or any(output_dir.iterdir()):
            raise ValueError("fixture output must be a missing or empty directory")
    else:
        output_dir.mkdir(parents=True)

    if manifest_output is not None and manifest_output.parent.resolve() == output_dir.resolve():
        raise ValueError("the fixture manifest must be outside the exact input directory")

    frames = _trusted_frames(adversarial_output_write=adversarial_output_write)
    for stem, frame in sorted(frames.items()):
        frame.to_pickle(
            output_dir / f"{stem}.gz",
            compression={"method": "gzip", "mtime": 0},
            protocol=5,
        )

    actual_names = {path.name for path in output_dir.iterdir()}
    if actual_names != set(EXPECTED_FILENAMES):
        raise RuntimeError("fixture generation did not produce the exact four-file input set")

    if manifest_output is None:
        return
    manifest_output.parent.mkdir(parents=True, exist_ok=True)
    files = []
    for name in EXPECTED_FILENAMES:
        size, checksum = _file_identity(output_dir / name)
        files.append(
            {
                "name": name,
                "url": f"fixture://{name}",
                "size_bytes": size,
                "checksum_algorithm": "md5",
                "checksum": checksum,
            }
        )
    manifest = {
        "schema_version": "yieldforge.dataset-source.v1",
        "dataset_id": "lectra-trusted-fixture",
        "title": "Trusted Lectra qualifier smoke fixture",
        "doi": "test-only",
        "version": "1",
        "license": "test-only",
        "source_page": "fixture://local",
        "files": files,
    }
    manifest_output.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path)
    parser.add_argument("--adversarial-output-write", action="store_true")
    args = parser.parse_args()
    write_fixture(
        args.output,
        args.manifest_output,
        adversarial_output_write=args.adversarial_output_write,
    )


if __name__ == "__main__":
    main()
