"""Canonical write-once archive for generated order-book manifests."""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

from pydantic import ValidationError

from yieldforge.order_books.domain import (
    OrderBookManifest,
    canonical_json_bytes,
    manifest_content_sha256,
)

_MAX_MANIFEST_BYTES = 8 * 1024 * 1024


class ArchiveIntegrityError(ValueError):
    """A manifest or archive path failed an integrity check."""


class ArchiveCollisionError(FileExistsError):
    """A canonical manifest path already exists and will not be overwritten."""


def _canonical_manifest_bytes(manifest: OrderBookManifest) -> bytes:
    return canonical_json_bytes(manifest.model_dump(mode="json")) + b"\n"


def _bind_source_task_references(manifest: OrderBookManifest) -> None:
    from yieldforge.order_books.generator import committed_source_task_references

    expected = {
        reference.tasks_index: reference for reference in committed_source_task_references()
    }
    for event in manifest.events:
        if expected.get(event.source_task.tasks_index) != event.source_task:
            raise ArchiveIntegrityError(
                "source-task reference does not match the pinned normalized slice"
            )


def _require_deterministic_generator_replay(manifest: OrderBookManifest) -> None:
    from yieldforge.order_books.generator import COMMITTED_SLICE_PATH, generate_order_book

    try:
        expected = generate_order_book(manifest.request, COMMITTED_SLICE_PATH)
    except ValueError as error:
        raise ArchiveIntegrityError(f"deterministic generator replay failed: {error}") from error
    if manifest != expected:
        raise ArchiveIntegrityError(
            "manifest does not match deterministic generator replay for its request"
        )


def _open_archive_directory(directory: Path) -> int:
    if directory.is_symlink():
        raise ArchiveIntegrityError("archive directory must not be a symlink")
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise ArchiveIntegrityError("archive directory must not be a symlink")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(directory, flags)
    except OSError as error:
        raise ArchiveIntegrityError(f"cannot securely open archive directory: {error}") from error


def write_manifest(manifest: OrderBookManifest, directory: Path) -> Path:
    """Atomically publish a canonical manifest without replacing any existing path."""

    data = _canonical_manifest_bytes(manifest)
    try:
        validated = OrderBookManifest.model_validate_json(data)
    except ValidationError as error:
        raise ArchiveIntegrityError(
            f"manifest validation failed before archive publication: {error}"
        ) from error
    _bind_source_task_references(validated)
    _require_deterministic_generator_replay(validated)
    if manifest_content_sha256(manifest) != manifest.content_sha256:
        raise ArchiveIntegrityError("content hash mismatch")
    if len(data) > _MAX_MANIFEST_BYTES:
        raise ArchiveIntegrityError("manifest exceeds archive size limit")

    directory_fd = _open_archive_directory(directory)
    final_name = f"{manifest.order_book_id}.json"
    temp_name = f".{manifest.order_book_id}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    temp_fd: int | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        temp_fd = os.open(temp_name, flags, 0o644, dir_fd=directory_fd)
        with os.fdopen(temp_fd, "wb", closefd=True) as output:
            temp_fd = None
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(
                temp_name,
                final_name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileExistsError as error:
            raise ArchiveCollisionError(f"archive manifest already exists: {final_name}") from error
        os.unlink(temp_name, dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        if temp_fd is not None:
            os.close(temp_fd)
        try:
            os.unlink(temp_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)
    return directory / final_name


def read_manifest(path: Path) -> OrderBookManifest:
    """Read a bounded regular file and revalidate its content-addressed identity."""

    file_stat = path.lstat()
    if stat.S_ISLNK(file_stat.st_mode):
        raise ArchiveIntegrityError("manifest path must not be a symlink")
    if not stat.S_ISREG(file_stat.st_mode):
        raise ArchiveIntegrityError("manifest path must be a regular file")
    if file_stat.st_size > _MAX_MANIFEST_BYTES:
        raise ArchiveIntegrityError("manifest exceeds archive size limit")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        data = os.read(descriptor, _MAX_MANIFEST_BYTES + 1)
    finally:
        os.close(descriptor)
    try:
        manifest = OrderBookManifest.model_validate_json(data)
    except ValidationError as error:
        raise ArchiveIntegrityError(f"manifest validation failed: {error}") from error
    if path.name != f"{manifest.order_book_id}.json":
        raise ArchiveIntegrityError("manifest filename does not match order book ID")
    if data != _canonical_manifest_bytes(manifest):
        raise ArchiveIntegrityError("manifest bytes do not use canonical archive encoding")
    _bind_source_task_references(manifest)
    _require_deterministic_generator_replay(manifest)
    return manifest


def verify_manifest_file(path: Path) -> str:
    """Return the verified content hash for one canonical archive file."""

    return read_manifest(path).content_sha256
