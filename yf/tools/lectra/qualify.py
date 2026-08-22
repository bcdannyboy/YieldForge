"""Qualify the pinned Lectra pickle release inside the locked container boundary."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, BinaryIO

from yieldforge.datasets.source_manifest import DatasetSourceManifest, SourceFile

EXPECTED_FILENAMES = frozenset({"constraints.gz", "parts.gz", "shapes.gz", "tasks.gz"})
QUALIFIER_MODES = frozenset({"audit", "catalog", "slice"})
SLICE_NAME = "lectra-slice.json"
CATALOG_NAME = "lectra-catalog.json"
APP_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = APP_ROOT / "datasets" / "sources" / "lectra-7030786-v1.1.json"
INPUT_DIR = Path("/input")
HASH_CHUNK_BYTES = 1024 * 1024
MAX_REPORT_BYTES = 4 * 1024 * 1024
MAX_CATALOG_BYTES = 64 * 1024 * 1024
MAX_CGROUP_VALUE_BYTES = 64
MAX_TELEMETRY_LINE_BYTES = 256
CGROUP_MEMORY_FILES = (
    ("memory.current", Path("/sys/fs/cgroup/memory.current")),
    ("memory.peak", Path("/sys/fs/cgroup/memory.peak")),
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class QualificationBoundaryError(RuntimeError):
    """The mounted data violates the qualifier's closed boundary."""


def _load_manifest(path: Path = MANIFEST_PATH) -> DatasetSourceManifest:
    if path.is_symlink() or not path.is_file():
        raise QualificationBoundaryError("the pinned source manifest is unavailable")
    manifest = DatasetSourceManifest.model_validate_json(path.read_text(encoding="utf-8"))
    if {source.name for source in manifest.files} != EXPECTED_FILENAMES:
        raise QualificationBoundaryError("the pinned manifest must name exactly four release files")
    return manifest


def _require_linux_memfd_sealing() -> None:
    required = (
        "F_ADD_SEALS",
        "F_GET_SEALS",
        "F_SEAL_WRITE",
        "F_SEAL_GROW",
        "F_SEAL_SHRINK",
        "F_SEAL_SEAL",
    )
    if sys.platform != "linux" or not hasattr(os, "memfd_create"):
        raise QualificationBoundaryError("qualification requires Linux sealed memfd support")
    if any(not hasattr(fcntl, name) for name in required):
        raise QualificationBoundaryError("the Linux runtime does not expose required memfd seals")


def _copy_verified_to_memfd(source_fd: int, source: SourceFile) -> int:
    flags = os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING
    memory_fd = os.memfd_create(f"lectra-{source.name}", flags)
    try:
        digest = hashlib.md5(usedforsecurity=False)
        byte_count = 0
        while chunk := os.read(source_fd, HASH_CHUNK_BYTES):
            byte_count += len(chunk)
            if byte_count > source.size_bytes:
                raise QualificationBoundaryError(f"{source.name} exceeds its pinned size")
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(memory_fd, view)
                if written <= 0:
                    raise QualificationBoundaryError("failed to stage verified input")
                view = view[written:]

        if byte_count != source.size_bytes:
            raise QualificationBoundaryError(
                f"{source.name} size mismatch: expected {source.size_bytes}, got {byte_count}"
            )
        if digest.hexdigest() != source.checksum:
            raise QualificationBoundaryError(f"{source.name} MD5 mismatch")

        required_seals = (
            fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
        )
        fcntl.fcntl(memory_fd, fcntl.F_ADD_SEALS, required_seals)
        applied_seals = fcntl.fcntl(memory_fd, fcntl.F_GET_SEALS)
        if applied_seals & required_seals != required_seals:
            raise QualificationBoundaryError(f"failed to seal staged bytes for {source.name}")
        os.lseek(memory_fd, 0, os.SEEK_SET)
        return memory_fd
    except BaseException:
        os.close(memory_fd)
        raise


@contextmanager
def _verified_memfds(
    input_dir: Path, manifest: DatasetSourceManifest
) -> Iterator[dict[str, BinaryIO]]:
    """Yield sealed handles containing the exact bytes that passed verification."""
    _require_linux_memfd_sealing()
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    with ExitStack() as stack:
        try:
            directory_fd = os.open(input_dir, directory_flags)
        except OSError as error:
            raise QualificationBoundaryError("/input must be a real mounted directory") from error
        stack.callback(os.close, directory_fd)

        actual_names = set(os.listdir(directory_fd))
        if actual_names != EXPECTED_FILENAMES:
            raise QualificationBoundaryError("/input must contain exactly the four release files")

        handles: dict[str, BinaryIO] = {}
        for source in sorted(manifest.files, key=lambda item: item.name):
            metadata = os.stat(source.name, dir_fd=directory_fd, follow_symlinks=False)
            if not stat.S_ISREG(metadata.st_mode):
                raise QualificationBoundaryError("/input entries must be regular files, not links")
            if metadata.st_size != source.size_bytes:
                raise QualificationBoundaryError(
                    f"{source.name} size mismatch: expected {source.size_bytes}, "
                    f"got {metadata.st_size}"
                )

            source_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
            try:
                source_fd = os.open(source.name, source_flags, dir_fd=directory_fd)
            except OSError as error:
                raise QualificationBoundaryError(f"could not safely open {source.name}") from error
            stack.callback(os.close, source_fd)
            if not stat.S_ISREG(os.fstat(source_fd).st_mode):
                raise QualificationBoundaryError("opened input is not a regular file")

            memory_fd = _copy_verified_to_memfd(source_fd, source)
            stack.callback(os.close, memory_fd)
            handle = stack.enter_context(os.fdopen(os.dup(memory_fd), "rb", closefd=True))
            handles[source.name.removesuffix(".gz")] = handle

        if set(os.listdir(directory_fd)) != EXPECTED_FILENAMES:
            raise QualificationBoundaryError("/input changed while qualification was staging")
        yield handles


def _read_verified_pickle(handle: BinaryIO) -> Any:
    # pandas stays inside this one production entry point by design.
    import pandas as pd

    return pd.read_pickle(handle, compression="gzip")


def _read_cgroup_counter(path: Path) -> str | None:
    """Read one bounded decimal cgroup-v2 counter, or omit it when unavailable."""
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        payload = os.read(descriptor, MAX_CGROUP_VALUE_BYTES + 1)
    except OSError:
        return None
    finally:
        if descriptor is not None:
            os.close(descriptor)

    value = payload.strip()
    if not value or len(payload) > MAX_CGROUP_VALUE_BYTES or not value.isdigit():
        return None
    return value.decode("ascii")


def _emit_stage_telemetry(stage: str) -> None:
    """Emit one bounded diagnostic line without ever using the report stream."""
    safe_stage = "".join(
        character
        for character in stage
        if character.isascii() and (character.isalnum() or character in "._-")
    )[:64]
    if not safe_stage:
        safe_stage = "unknown"
    fields = [f"stage={safe_stage}"]
    for label, path in CGROUP_MEMORY_FILES:
        if value := _read_cgroup_counter(path):
            fields.append(f"{label}={value}")
    payload = (" ".join(fields) + "\n").encode("ascii")
    if len(payload) > MAX_TELEMETRY_LINE_BYTES:
        payload = f"stage={safe_stage}\n".encode("ascii")
    _write_all(sys.stderr.fileno(), payload)


def _qualify_payload(
    mode: str = "audit",
    source_manifest_sha256: str | None = None,
    audit_report_sha256: str | None = None,
) -> bytes:
    from yieldforge.datasets.lectra_audit import (
        audit_frames,
        report_to_json,
        validate_frame_schema,
    )

    if mode not in QUALIFIER_MODES:
        raise QualificationBoundaryError(f"unknown qualification mode: {mode!r}")
    if mode in {"catalog", "slice"}:
        if not source_manifest_sha256 or not _SHA256_PATTERN.fullmatch(source_manifest_sha256):
            raise QualificationBoundaryError(f"{mode} mode requires a lowercase manifest SHA-256")
        if not audit_report_sha256 or not _SHA256_PATTERN.fullmatch(audit_report_sha256):
            raise QualificationBoundaryError(f"{mode} mode requires a lowercase audit SHA-256")
        actual_manifest_sha256 = hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest()
        if actual_manifest_sha256 != source_manifest_sha256:
            raise QualificationBoundaryError(
                "mounted manifest hash differs from the trusted runner"
            )
    elif source_manifest_sha256 is not None or audit_report_sha256 is not None:
        raise QualificationBoundaryError("audit mode does not accept export evidence hashes")

    manifest = _load_manifest()
    with _verified_memfds(INPUT_DIR, manifest) as staged:
        _emit_stage_telemetry("sealed-staging")
        frames = {}
        for name, handle in sorted(staged.items()):
            frame = _read_verified_pickle(handle)
            validate_frame_schema(name, frame)
            frames[name] = frame
            _emit_stage_telemetry(f"table-{name}-validated")
    if mode == "audit":
        report = audit_frames(
            frames,
            dataset_id=manifest.dataset_id,
            source_checksums={source.name: source.checksum for source in manifest.files},
        )
        _emit_stage_telemetry("audit-complete")
        payload = (report_to_json(report, indent=2) + "\n").encode("utf-8")
    elif mode == "slice":
        from yieldforge.datasets.lectra_slice import export_representative_slice

        assert source_manifest_sha256 is not None
        assert audit_report_sha256 is not None
        normalized = export_representative_slice(
            frames,
            manifest=manifest,
            source_manifest_sha256=source_manifest_sha256,
            audit_report_sha256=audit_report_sha256,
        )
        _emit_stage_telemetry("slice-complete")
        payload = (
            json.dumps(
                normalized.model_dump(mode="json"),
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    else:
        from yieldforge.datasets.lectra_slice import export_catalog_slice

        assert source_manifest_sha256 is not None
        assert audit_report_sha256 is not None
        normalized = export_catalog_slice(
            frames,
            manifest=manifest,
            source_manifest_sha256=source_manifest_sha256,
            audit_report_sha256=audit_report_sha256,
        )
        _emit_stage_telemetry("catalog-complete")
        payload = (
            json.dumps(
                normalized.model_dump(mode="json"),
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    max_bytes = MAX_CATALOG_BYTES if mode == "catalog" else MAX_REPORT_BYTES
    if len(payload) > max_bytes:
        raise QualificationBoundaryError("qualification artifact exceeds the mode size limit")
    return payload


def _write_all(file_descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(file_descriptor, view)
        if written <= 0:
            raise QualificationBoundaryError("failed to write qualifier output")
        view = view[written:]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=sorted(QUALIFIER_MODES), default="audit")
    parser.add_argument("--source-manifest-sha256")
    parser.add_argument("--audit-report-sha256")
    args = parser.parse_args(argv)
    if args.mode in {"catalog", "slice"} and (
        args.source_manifest_sha256 is None or args.audit_report_sha256 is None
    ):
        parser.error(f"{args.mode} mode requires both evidence SHA-256 arguments")
    if args.mode == "audit" and (
        args.source_manifest_sha256 is not None or args.audit_report_sha256 is not None
    ):
        parser.error("audit mode does not accept export evidence hashes")
    return args


def _entrypoint() -> None:
    """Suppress untrusted stdout until one final JSON payload is ready."""
    report_fd = os.dup(sys.stdout.fileno())
    os.set_inheritable(report_fd, False)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    try:
        args = _parse_args()
        payload = _qualify_payload(
            mode=args.mode,
            source_manifest_sha256=args.source_manifest_sha256,
            audit_report_sha256=args.audit_report_sha256,
        )
        sys.stdout.flush()
        _write_all(report_fd, payload)
        _write_all(sys.stderr.fileno(), b"qualification completed\n")
        exit_code = 0
    except BaseException as error:
        error_name = type(error).__name__.encode("ascii", errors="replace")
        _write_all(sys.stderr.fileno(), b"qualification failed: " + error_name + b"\n")
        exit_code = 1
    finally:
        os.close(report_fd)
    os._exit(exit_code)


if __name__ == "__main__":
    _entrypoint()
