"""Verified, interpretation-free acquisition of pinned dataset files."""

import hashlib
import urllib.request
from enum import StrEnum
from pathlib import Path

from yieldforge.datasets.source_manifest import SourceFile


class DatasetIntegrityError(ValueError):
    """Raised when local bytes do not match their pinned source contract."""


class FetchStatus(StrEnum):
    """The two successful outcomes of a verified fetch."""

    DOWNLOADED = "downloaded"
    ALREADY_VERIFIED = "already verified"


def _file_identity(path: Path, chunk_size: int) -> tuple[int, str]:
    byte_count = 0
    digest = hashlib.md5()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            byte_count += len(chunk)
            digest.update(chunk)
    return byte_count, digest.hexdigest()


def _require_identity(spec: SourceFile, byte_count: int, checksum: str, *, subject: str) -> None:
    if byte_count != spec.size_bytes:
        raise DatasetIntegrityError(
            f"{subject} byte count {byte_count} does not match expected {spec.size_bytes}"
        )
    if checksum != spec.checksum:
        raise DatasetIntegrityError(
            f"{subject} md5 {checksum} does not match expected {spec.checksum}"
        )


def fetch_file(spec: SourceFile, destination: Path, chunk_size: int = 1024 * 1024) -> FetchStatus:
    """Stream one source to disk and promote it only after full verification.

    Existing verified files are reused. Existing files with any other identity
    are never overwritten, making accidental corpus drift visible to callers.
    """

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")

    partial = destination.with_name(destination.name + ".partial")
    partial.unlink(missing_ok=True)

    if destination.exists():
        if not destination.is_file():
            raise DatasetIntegrityError(f"existing destination is not a file: {destination}")
        byte_count, checksum = _file_identity(destination, chunk_size)
        try:
            _require_identity(spec, byte_count, checksum, subject="existing destination")
        except DatasetIntegrityError as error:
            raise DatasetIntegrityError(
                f"refusing to overwrite existing destination {destination}: {error}"
            ) from error
        return FetchStatus.ALREADY_VERIFIED

    destination.parent.mkdir(parents=True, exist_ok=True)
    promoted = False
    try:
        byte_count = 0
        digest = hashlib.md5()
        with urllib.request.urlopen(spec.url) as response, partial.open("wb") as output:
            while chunk := response.read(chunk_size):
                output.write(chunk)
                byte_count += len(chunk)
                digest.update(chunk)

        _require_identity(spec, byte_count, digest.hexdigest(), subject="download")
        partial.replace(destination)
        promoted = True
        return FetchStatus.DOWNLOADED
    finally:
        if not promoted:
            partial.unlink(missing_ok=True)
