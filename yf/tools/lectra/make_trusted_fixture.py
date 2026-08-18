"""Create tiny trusted Lectra-shaped pickle files for container smoke tests."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

EXPECTED_FILENAMES = ("parts.gz", "constraints.gz", "shapes.gz", "tasks.gz")


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
                "duration": 3,
                "sheet_width": 10.0,
                "sheet_length": 20.0,
                "sheet_type": 0,
                "tasks_index": 100,
                "is_train": True,
                "is_val": False,
                "is_test": False,
            },
            {
                "efficiency": 0.4,
                "duration": 4,
                "sheet_width": 12.0,
                "sheet_length": 25.0,
                "sheet_type": 0,
                "tasks_index": 900,
                "is_train": True,
                "is_val": False,
                "is_test": False,
            },
        ]
    )
    if adversarial_output_write:
        tasks["adversarial_probe"] = [_AttemptHostOutputWrite(), None]

    shapes = pd.DataFrame(
        [
            {
                "shape_hash": 10_000 + offset,
                "raw": [0.0, 0.0, float(offset + 1), 0.0, 0.0, float(offset + 1)],
                "sizes": [6],
            }
            for offset in range(9)
        ]
    )
    parts = pd.DataFrame(
        [
            {
                "tasks_index": 100,
                "part_id": 100_000 + offset,
                "shape_hash": 10_000 + offset % 9,
            }
            for offset in range(35)
        ]
        + [
            {
                "tasks_index": 900,
                "part_id": 900_000 + offset,
                "shape_hash": 10_000 + offset % 5,
            }
            for offset in range(20)
        ]
    )
    constraint_columns = [
        "parts_1",
        "p1_x",
        "p1_y",
        "r1_start",
        "r1_end",
        "r1_flip_x",
        "parts_2",
        "p2_x",
        "p2_y",
        "x_offset",
        "y_offset",
        "motif_order",
        "x_alignment_type",
        "y_alignment_type",
        "proximity_type",
        "max_distance",
        "y_min",
        "y_max",
        "groups_relative_orientation",
        "is_frozen",
        "tasks_index",
        "type",
    ]
    constraints_data = []
    for offset in range(35):
        constraint = dict.fromkeys(constraint_columns)
        constraint.update(
            {
                "parts_1": [100_000 + offset],
                "r1_start": [0.0, 90.0],
                "r1_end": [0.0, 90.0],
                "r1_flip_x": [0, 0],
                "tasks_index": 100,
                "type": "s1",
            }
        )
        constraints_data.append(constraint)
    view_constraint = dict.fromkeys(constraint_columns)
    view_constraint.update({"parts_1": [900_000], "tasks_index": 900, "type": "c1"})
    constraints_data.append(view_constraint)
    constraints = pd.DataFrame(constraints_data, columns=constraint_columns)
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
        "dataset_id": "lectra-7030786-v1.1",
        "title": "Trusted Lectra qualifier smoke fixture",
        "doi": "10.5281/zenodo.7030786",
        "version": "1.1",
        "license": "CC-BY-4.0",
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
