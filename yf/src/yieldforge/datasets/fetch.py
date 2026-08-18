"""Verified, interpretation-free acquisition of pinned dataset files."""

import hashlib
import math
import os
import stat
import tempfile
import urllib.request
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO

from yieldforge.datasets.source_manifest import SourceFile

DEFAULT_FETCH_TIMEOUT_SECONDS = 30.0


class DatasetIntegrityError(ValueError):
    """Raised when local bytes do not match their pinned source contract."""


class FetchStatus(StrEnum):
    """The two successful outcomes of a verified fetch."""

    DOWNLOADED = "downloaded"
    ALREADY_VERIFIED = "already verified"


def _file_identity(path: Path, chunk_size: int) -> tuple[int, str]:
    byte_count = 0
    digest = hashlib.md5()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise DatasetIntegrityError(f"existing destination is not a regular file: {path}")
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


def _verified_existing_destination(spec: SourceFile, destination: Path, chunk_size: int) -> bool:
    try:
        destination_mode = destination.lstat().st_mode
    except FileNotFoundError:
        return False

    if stat.S_ISLNK(destination_mode):
        raise DatasetIntegrityError(f"existing destination is a symlink: {destination}")
    if not stat.S_ISREG(destination_mode):
        raise DatasetIntegrityError(f"existing destination is not a regular file: {destination}")

    try:
        byte_count, checksum = _file_identity(destination, chunk_size)
    except FileNotFoundError:
        return False
    except OSError as error:
        raise DatasetIntegrityError(
            f"could not safely inspect existing destination {destination}: {error}"
        ) from error

    try:
        _require_identity(spec, byte_count, checksum, subject="existing destination")
    except DatasetIntegrityError as error:
        raise DatasetIntegrityError(
            f"refusing to overwrite existing destination {destination}: {error}"
        ) from error
    return True


def _promote_without_clobbering(
    spec: SourceFile, partial: Path, destination: Path, chunk_size: int
) -> FetchStatus:
    while True:
        try:
            os.link(partial, destination)
            return FetchStatus.DOWNLOADED
        except FileExistsError:
            if _verified_existing_destination(spec, destination, chunk_size):
                return FetchStatus.ALREADY_VERIFIED


def fetch_file(
    spec: SourceFile,
    destination: Path,
    chunk_size: int = 1024 * 1024,
    timeout_seconds: float = DEFAULT_FETCH_TIMEOUT_SECONDS,
) -> FetchStatus:
    """Stream one source to disk and promote it only after full verification.

    Existing verified files are reused. Existing files with any other identity
    are never overwritten, making accidental corpus drift visible to callers.
    """

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if timeout_seconds <= 0 or not math.isfinite(timeout_seconds):
        raise ValueError("timeout_seconds must be positive and finite")

    if _verified_existing_destination(spec, destination, chunk_size):
        return FetchStatus.ALREADY_VERIFIED

    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, partial_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".partial",
    )
    partial = Path(partial_name)
    output: BinaryIO | None = None
    try:
        output = os.fdopen(descriptor, "wb")
        byte_count = 0
        digest = hashlib.md5()
        with output:
            with urllib.request.urlopen(spec.url, timeout=timeout_seconds) as response:
                while chunk := response.read(chunk_size):
                    output.write(chunk)
                    byte_count += len(chunk)
                    digest.update(chunk)

        _require_identity(spec, byte_count, digest.hexdigest(), subject="download")
        return _promote_without_clobbering(spec, partial, destination, chunk_size)
    finally:
        if output is None:
            os.close(descriptor)
        elif not output.closed:
            output.close()
        partial.unlink(missing_ok=True)
