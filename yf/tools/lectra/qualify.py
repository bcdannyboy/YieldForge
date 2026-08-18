"""Qualify the pinned Lectra pickle release inside the locked container boundary."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

from yieldforge.datasets.source_manifest import DatasetSourceManifest

EXPECTED_FILENAMES = frozenset({"constraints.gz", "parts.gz", "shapes.gz", "tasks.gz"})
APP_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = APP_ROOT / "datasets" / "sources" / "lectra-7030786-v1.1.json"
INPUT_DIR = Path("/input")
OUTPUT_DIR = Path("/output")
REPORT_NAME = "lectra-audit.json"
HASH_CHUNK_BYTES = 1024 * 1024


class QualificationBoundaryError(RuntimeError):
    """The mounted data or output violates the qualifier's closed boundary."""


def _load_manifest(path: Path = MANIFEST_PATH) -> DatasetSourceManifest:
    if path.is_symlink() or not path.is_file():
        raise QualificationBoundaryError("the pinned source manifest is unavailable")
    return DatasetSourceManifest.model_validate_json(path.read_text(encoding="utf-8"))


def _md5_and_size(path: Path) -> tuple[int, str]:
    digest = hashlib.md5(usedforsecurity=False)
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(HASH_CHUNK_BYTES):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _verify_input(input_dir: Path, manifest: DatasetSourceManifest) -> dict[str, Path]:
    """Require the exact release file set and verify it before deserialization."""
    if input_dir.is_symlink() or not input_dir.is_dir():
        raise QualificationBoundaryError("/input must be a mounted directory")

    manifest_names = {source.name for source in manifest.files}
    if manifest_names != EXPECTED_FILENAMES:
        raise QualificationBoundaryError("the pinned manifest must name exactly four release files")

    entries = list(input_dir.iterdir())
    actual_names = {entry.name for entry in entries}
    if actual_names != EXPECTED_FILENAMES or len(entries) != len(EXPECTED_FILENAMES):
        raise QualificationBoundaryError("/input must contain exactly the four release files")
    if any(entry.is_symlink() or not entry.is_file() for entry in entries):
        raise QualificationBoundaryError("/input entries must be regular files, not links")

    verified: dict[str, Path] = {}
    for source in sorted(manifest.files, key=lambda item: item.name):
        path = input_dir / source.name
        size, checksum = _md5_and_size(path)
        if size != source.size_bytes:
            raise QualificationBoundaryError(
                f"{source.name} size mismatch: expected {source.size_bytes}, got {size}"
            )
        if checksum != source.checksum:
            raise QualificationBoundaryError(f"{source.name} MD5 mismatch")
        verified[source.name] = path
    return verified


def _require_empty_output(output_dir: Path) -> None:
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise QualificationBoundaryError("/output must be a mounted directory")
    if any(output_dir.iterdir()):
        raise QualificationBoundaryError("/output must be empty before qualification")


def _atomic_write_report(output_dir: Path, payload: str) -> Path:
    """Publish one complete report into a previously empty output mount."""
    _require_empty_output(output_dir)
    destination = output_dir / REPORT_NAME
    descriptor, temporary_name = tempfile.mkstemp(
        dir=output_dir, prefix=f".{REPORT_NAME}.", suffix=".tmp", text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if destination.exists() or destination.is_symlink():
            raise QualificationBoundaryError("the audit report already exists")
        os.replace(temporary, destination)
        directory_descriptor = os.open(output_dir, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def _read_verified_pickle(path: Path) -> Any:
    # pandas stays inside this one production entry point by design.
    import pandas as pd

    return pd.read_pickle(path, compression="gzip")


def main() -> None:
    from yieldforge.datasets.lectra_audit import audit_frames, report_to_json

    manifest = _load_manifest()
    verified = _verify_input(INPUT_DIR, manifest)
    _require_empty_output(OUTPUT_DIR)

    frames = {
        name.removesuffix(".gz"): _read_verified_pickle(path)
        for name, path in sorted(verified.items())
    }
    report = audit_frames(
        frames,
        dataset_id=manifest.dataset_id,
        source_checksums={source.name: source.checksum for source in manifest.files},
    )
    payload = report_to_json(report, indent=2) + "\n"
    destination = _atomic_write_report(OUTPUT_DIR, payload)
    print(f"wrote {destination}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"qualification failed: {error}") from error
