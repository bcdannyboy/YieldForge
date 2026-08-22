"""Run the untrusted Lectra qualifier and publish one validated passive report."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal

from yieldforge.datasets.lectra_audit import LectraAuditReport
from yieldforge.datasets.normalized_slice import NormalizedSlice
from yieldforge.datasets.passive_report import (
    PassiveEvidenceError,
    bind_lectra_audit_report,
    bind_normalized_slice_evidence,
    parse_dataset_source_manifest,
    parse_lectra_audit_report,
    parse_normalized_slice,
    read_passive_evidence_file,
)
from yieldforge.datasets.source_manifest import DatasetSourceManifest

EXPECTED_FILENAMES = frozenset({"constraints.gz", "parts.gz", "shapes.gz", "tasks.gz"})
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = PROJECT_ROOT / "datasets" / "sources" / "lectra-7030786-v1.1.json"
DEFAULT_IMAGE = "yieldforge-lectra-qualifier:7030786-v1.1"
REPORT_NAME = "lectra-audit.json"
SLICE_NAME = "lectra-slice.json"
CATALOG_NAME = "lectra-catalog.json"
MAX_REPORT_BYTES = 4 * 1024 * 1024
MAX_CATALOG_BYTES = 64 * 1024 * 1024
MAX_STDERR_BYTES = 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 15 * 60
_IMAGE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,254}$")
type QualifierMode = Literal["audit", "catalog", "slice"]
type NormalizedRuleset = Literal["lectra-catalog-rules.v1", "lectra-slice-rules.v1"]


class QualifierRunnerError(RuntimeError):
    """Qualification or trusted publication failed closed."""


def _mode_max_bytes(mode: QualifierMode) -> int:
    return MAX_CATALOG_BYTES if mode == "catalog" else MAX_REPORT_BYTES


@dataclass(frozen=True)
class _OutputDirectory:
    path: Path
    file_descriptor: int
    device: int
    inode: int


class _LimitedCapture:
    def __init__(self, limit: int, overflow_event: threading.Event) -> None:
        if limit <= 0:
            raise ValueError("capture limit must be positive")
        self.limit = limit
        self.overflow_event = overflow_event
        self.buffer = bytearray()
        self.total = 0
        self.overflowed = False
        self._lock = threading.Lock()

    def append(self, chunk: bytes) -> None:
        with self._lock:
            self.total += len(chunk)
            remaining = self.limit - len(self.buffer)
            if remaining > 0:
                self.buffer.extend(chunk[:remaining])
            if self.total > self.limit:
                self.overflowed = True
                self.overflow_event.set()


def _drain_pipe(pipe: BinaryIO, capture: _LimitedCapture) -> None:
    try:
        while chunk := pipe.read(64 * 1024):
            capture.append(chunk)
    finally:
        pipe.close()


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def _capture_process(
    command: Sequence[str],
    *,
    timeout_seconds: float,
    stdout_limit: int,
    stderr_limit: int,
    on_abort: Callable[[], None],
) -> tuple[int, bytes, bytes]:
    """Capture a child without ever retaining more than the declared limits."""
    if timeout_seconds <= 0:
        raise ValueError("timeout must be positive")
    process = subprocess.Popen(
        list(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        start_new_session=True,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    overflow_event = threading.Event()
    stdout_capture = _LimitedCapture(stdout_limit, overflow_event)
    stderr_capture = _LimitedCapture(stderr_limit, overflow_event)
    threads = [
        threading.Thread(target=_drain_pipe, args=(process.stdout, stdout_capture), daemon=True),
        threading.Thread(target=_drain_pipe, args=(process.stderr, stderr_capture), daemon=True),
    ]
    for thread in threads:
        thread.start()

    deadline = time.monotonic() + timeout_seconds
    abort_reason: str | None = None
    while process.poll() is None:
        if overflow_event.wait(timeout=0.02):
            if stdout_capture.overflowed:
                abort_reason = "qualifier exceeded the stdout size limit"
            else:
                abort_reason = "qualifier exceeded the stderr size limit"
            break
        if time.monotonic() >= deadline:
            abort_reason = "qualifier exceeded its runtime timeout"
            break

    cleanup_error: BaseException | None = None
    if abort_reason is not None:
        try:
            on_abort()
        except BaseException as error:
            cleanup_error = error
        finally:
            _terminate_process(process)
    else:
        process.wait()

    for thread in threads:
        thread.join(timeout=2)
    if any(thread.is_alive() for thread in threads):
        _terminate_process(process)
        if cleanup_error is not None:
            raise cleanup_error
        raise QualifierRunnerError("qualifier output pipes did not close")

    if cleanup_error is not None:
        raise cleanup_error

    if abort_reason is None and (stdout_capture.overflowed or stderr_capture.overflowed):
        on_abort()
        if stdout_capture.overflowed:
            abort_reason = "qualifier exceeded the stdout size limit"
        else:
            abort_reason = "qualifier exceeded the stderr size limit"
    if abort_reason is not None:
        raise QualifierRunnerError(abort_reason)
    return process.returncode, bytes(stdout_capture.buffer), bytes(stderr_capture.buffer)


def _load_manifest(path: Path) -> DatasetSourceManifest:
    try:
        payload = read_passive_evidence_file(path, label="dataset source manifest")
        manifest = parse_dataset_source_manifest(payload)
    except PassiveEvidenceError as error:
        raise QualifierRunnerError(str(error)) from error
    if {source.name for source in manifest.files} != EXPECTED_FILENAMES:
        raise QualifierRunnerError("manifest must identify exactly the four Lectra files")
    return manifest


def _validate_input_dir(input_dir: Path, manifest: DatasetSourceManifest) -> Path:
    if input_dir.is_symlink() or not input_dir.is_dir():
        raise QualifierRunnerError("input must be a regular directory, not a link")
    resolved = input_dir.resolve(strict=True)
    if "," in str(resolved):
        raise QualifierRunnerError("input path cannot contain a comma")
    entries = list(input_dir.iterdir())
    expected_names = {source.name for source in manifest.files}
    if {entry.name for entry in entries} != expected_names or len(entries) != len(expected_names):
        raise QualifierRunnerError("input must contain exactly the four manifest files")
    if any(entry.is_symlink() or not entry.is_file() for entry in entries):
        raise QualifierRunnerError("input entries must be regular files, not links")
    return resolved


@contextmanager
def _open_output_dir(output_dir: Path) -> Iterator[_OutputDirectory]:
    """Hold the initially empty output directory by inode for the whole run."""
    absolute_path = output_dir.absolute()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        file_descriptor = os.open(absolute_path, flags)
    except OSError as error:
        raise QualifierRunnerError("output must be a regular directory, not a link") from error
    try:
        metadata = os.fstat(file_descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise QualifierRunnerError("output must be a regular directory, not a link")
        if os.listdir(file_descriptor):
            raise QualifierRunnerError("output directory must be empty")
        yield _OutputDirectory(
            path=absolute_path,
            file_descriptor=file_descriptor,
            device=metadata.st_dev,
            inode=metadata.st_ino,
        )
    finally:
        os.close(file_descriptor)


def _output_path_has_held_identity(output: _OutputDirectory) -> bool:
    try:
        metadata = os.stat(output.path, follow_symlinks=False)
    except OSError:
        return False
    return (
        stat.S_ISDIR(metadata.st_mode)
        and metadata.st_dev == output.device
        and metadata.st_ino == output.inode
    )


def _validate_report(
    payload: bytes,
    manifest: DatasetSourceManifest,
    *,
    max_bytes: int = MAX_REPORT_BYTES,
) -> LectraAuditReport:
    if not payload or len(payload) > max_bytes:
        raise QualifierRunnerError("qualifier report violates the size limit")
    try:
        report = parse_lectra_audit_report(payload, max_bytes=max_bytes)
        bind_lectra_audit_report(report, manifest)
    except PassiveEvidenceError as error:
        raise QualifierRunnerError(str(error)) from error
    return report


def _validate_slice(
    payload: bytes,
    manifest: DatasetSourceManifest,
    report: LectraAuditReport,
    *,
    manifest_payload: bytes,
    report_payload: bytes,
    expected_ruleset: NormalizedRuleset = "lectra-slice-rules.v1",
    max_bytes: int = MAX_REPORT_BYTES,
) -> NormalizedSlice:
    if not payload or len(payload) > max_bytes:
        raise QualifierRunnerError("qualifier slice violates the size limit")
    try:
        normalized = parse_normalized_slice(payload, max_bytes=max_bytes)
        bind_normalized_slice_evidence(
            normalized,
            report,
            manifest,
            report_payload=report_payload,
            manifest_payload=manifest_payload,
        )
        if normalized.source.conversion_ruleset_version != expected_ruleset:
            raise QualifierRunnerError(
                "normalized artifact ruleset does not match the requested qualifier mode"
            )
    except PassiveEvidenceError as error:
        raise QualifierRunnerError(str(error)) from error
    return normalized


def _canonical_report_bytes(report: LectraAuditReport) -> bytes:
    try:
        payload = (
            json.dumps(
                report.model_dump(mode="json"),
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise QualifierRunnerError(
            "validated report could not be serialized canonically"
        ) from error
    if len(payload) > MAX_REPORT_BYTES:
        raise QualifierRunnerError("canonical report violates the size limit")
    return payload


def _canonical_slice_bytes(
    normalized: NormalizedSlice,
    *,
    max_bytes: int = MAX_REPORT_BYTES,
) -> bytes:
    try:
        payload = (
            json.dumps(
                normalized.model_dump(mode="json"),
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise QualifierRunnerError("validated slice could not be serialized canonically") from error
    if len(payload) > max_bytes:
        raise QualifierRunnerError("canonical slice violates the size limit")
    return payload


def _unlink_owned(output: _OutputDirectory, name: str) -> None:
    try:
        os.unlink(name, dir_fd=output.file_descriptor)
    except FileNotFoundError:
        pass


def _read_published_report(
    output: _OutputDirectory,
    name: str = REPORT_NAME,
    *,
    max_bytes: int = MAX_REPORT_BYTES,
) -> bytes:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open(name, flags, dir_fd=output.file_descriptor)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise QualifierRunnerError("published report is not a regular file")
        if metadata.st_size > max_bytes:
            raise QualifierRunnerError("published report violates the size limit")
        chunks: list[bytes] = []
        byte_count = 0
        while chunk := os.read(descriptor, min(64 * 1024, max_bytes + 1 - byte_count)):
            byte_count += len(chunk)
            if byte_count > max_bytes:
                raise QualifierRunnerError("published report violates the size limit")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _publish_report(
    output: _OutputDirectory,
    payload: bytes,
    *,
    name: str = REPORT_NAME,
    max_bytes: int = MAX_REPORT_BYTES,
) -> Path:
    """Atomically link a complete file into an empty directory without overwriting."""
    if len(payload) > max_bytes:
        raise QualifierRunnerError("qualifier report violates the size limit")
    if not _output_path_has_held_identity(output):
        raise QualifierRunnerError("output directory identity changed during qualification")
    if os.listdir(output.file_descriptor):
        raise QualifierRunnerError("output directory must remain empty until publication")
    if name not in {CATALOG_NAME, REPORT_NAME, SLICE_NAME}:
        raise QualifierRunnerError("unknown passive artifact name")
    temporary = f".{name}.{uuid.uuid4().hex}.tmp"
    temporary_created = False
    destination_created = False
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
            dir_fd=output.file_descriptor,
        )
        temporary_created = True
        try:
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise QualifierRunnerError("failed to stage the passive report")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

        if set(os.listdir(output.file_descriptor)) != {temporary}:
            raise QualifierRunnerError("unknown output appeared during publication")
        try:
            os.link(
                temporary,
                name,
                src_dir_fd=output.file_descriptor,
                dst_dir_fd=output.file_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError as error:
            raise QualifierRunnerError("refusing to overwrite an existing artifact") from error
        destination_created = True
        os.unlink(temporary, dir_fd=output.file_descriptor)
        temporary_created = False
        os.fsync(output.file_descriptor)
        entries = os.listdir(output.file_descriptor)
        if set(entries) != {name} or len(entries) != 1:
            raise QualifierRunnerError("published output failed its exact postcondition")
        metadata = os.stat(name, dir_fd=output.file_descriptor, follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode):
            raise QualifierRunnerError("published report is not a regular file")
        if _read_published_report(output, name, max_bytes=max_bytes) != payload:
            raise QualifierRunnerError("published report bytes changed unexpectedly")
        if not _output_path_has_held_identity(output):
            raise QualifierRunnerError("output directory identity changed during qualification")
        return output.path / name
    except BaseException:
        if temporary_created:
            _unlink_owned(output, temporary)
        if destination_created:
            _unlink_owned(output, name)
        raise


def _nonroot_identity() -> tuple[int, int]:
    uid = os.getuid()
    gid = os.getgid()
    if uid == 0 or gid == 0:
        raise QualifierRunnerError("refusing to run the qualifier with a root UID or GID")
    return uid, gid


def _docker_command(
    *,
    image: str,
    container_name: str,
    input_dir: Path,
    uid: int,
    gid: int,
    qualifier_args: Sequence[str] = (),
) -> list[str]:
    if not _IMAGE_PATTERN.fullmatch(image) or image.startswith("-"):
        raise QualifierRunnerError("invalid qualifier image reference")
    command = [
        "docker",
        "run",
        "--pull",
        "never",
        "--name",
        container_name,
        "--init",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--pids-limit",
        "128",
        "--memory",
        "16g",
        "--memory-swap",
        "16g",
        "--cpus",
        "4",
        "--ulimit",
        "nofile=1024:1024",
        "--ulimit",
        "nproc=128:128",
        "--ipc",
        "none",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=64m",
        "--user",
        f"{uid}:{gid}",
        "--log-driver",
        "none",
        "--mount",
        f"type=bind,src={input_dir},dst=/input,readonly",
        image,
    ]
    command.extend(qualifier_args)
    return command


def _docker_cleanup_command(command: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=10,
        check=False,
        shell=False,
    )


def _inspect_proves_absence(
    result: subprocess.CompletedProcess[bytes], container_name: str
) -> bool:
    if result.returncode == 0:
        return False
    detail = (result.stdout + result.stderr).decode("utf-8", errors="replace").lower()
    expected_name = container_name.lower()
    return expected_name in detail and ("no such object" in detail or "no such container" in detail)


def _ensure_container_absent(container_name: str, *, attempts: int = 3) -> None:
    """Force removal and require Docker to prove the generated name is absent."""
    if attempts <= 0:
        raise ValueError("cleanup attempts must be positive")
    observations: list[str] = []
    for attempt in range(1, attempts + 1):
        try:
            removal = _docker_cleanup_command(["docker", "rm", "--force", container_name])
            observations.append(f"rm#{attempt}={removal.returncode}")
        except subprocess.TimeoutExpired:
            observations.append(f"rm#{attempt}=timeout")
        except OSError:
            observations.append(f"rm#{attempt}=oserror")

        try:
            inspection = _docker_cleanup_command(
                [
                    "docker",
                    "inspect",
                    "--type",
                    "container",
                    "--format",
                    "{{.Id}}",
                    container_name,
                ]
            )
            observations.append(f"inspect#{attempt}={inspection.returncode}")
            if _inspect_proves_absence(inspection, container_name):
                return
        except subprocess.TimeoutExpired:
            observations.append(f"inspect#{attempt}=timeout")
        except OSError:
            observations.append(f"inspect#{attempt}=oserror")
        if attempt < attempts:
            time.sleep(0.05)

    evidence = ", ".join(observations)
    raise QualifierRunnerError(
        f"could not prove generated container {container_name} absent after cleanup: {evidence}"
    )


def run_qualifier(
    *,
    image: str,
    input_dir: Path,
    output_dir: Path,
    manifest_path: Path,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    mode: QualifierMode = "audit",
    audit_report_path: Path | None = None,
) -> Path:
    if mode not in {"audit", "catalog", "slice"}:
        raise QualifierRunnerError(f"unknown qualifier mode: {mode!r}")
    max_bytes = _mode_max_bytes(mode)
    try:
        manifest_payload = read_passive_evidence_file(
            manifest_path, label="dataset source manifest"
        )
        manifest = parse_dataset_source_manifest(manifest_payload)
    except PassiveEvidenceError as error:
        raise QualifierRunnerError(str(error)) from error
    if {source.name for source in manifest.files} != EXPECTED_FILENAMES:
        raise QualifierRunnerError("manifest must identify exactly the four Lectra files")

    report: LectraAuditReport | None = None
    report_payload: bytes | None = None
    qualifier_args: tuple[str, ...] = ()
    if mode in {"catalog", "slice"}:
        if audit_report_path is None:
            raise QualifierRunnerError(f"{mode} mode requires an exact audit report")
        try:
            report_payload = read_passive_evidence_file(
                audit_report_path, label="Lectra audit report"
            )
            report = parse_lectra_audit_report(report_payload)
            bind_lectra_audit_report(report, manifest)
        except PassiveEvidenceError as error:
            raise QualifierRunnerError(str(error)) from error
        qualifier_args = (
            "--mode",
            mode,
            "--source-manifest-sha256",
            hashlib.sha256(manifest_payload).hexdigest(),
            "--audit-report-sha256",
            hashlib.sha256(report_payload).hexdigest(),
        )
    elif audit_report_path is not None:
        raise QualifierRunnerError("audit mode does not accept an audit report input")
    resolved_input = _validate_input_dir(input_dir, manifest)
    uid, gid = _nonroot_identity()
    container_name = f"yieldforge-lectra-{uuid.uuid4().hex}"
    command = _docker_command(
        image=image,
        container_name=container_name,
        input_dir=resolved_input,
        uid=uid,
        gid=gid,
        qualifier_args=qualifier_args,
    )
    with _open_output_dir(output_dir) as opened_output:
        try:
            return_code, stdout, stderr = _capture_process(
                command,
                timeout_seconds=timeout_seconds,
                stdout_limit=max_bytes,
                stderr_limit=MAX_STDERR_BYTES,
                on_abort=lambda: _ensure_container_absent(container_name),
            )
        finally:
            _ensure_container_absent(container_name)

        if return_code != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise QualifierRunnerError(
                f"qualifier container failed with exit {return_code}: {detail}"
            )
        if mode == "audit":
            validated_report = _validate_report(stdout, manifest)
            canonical_payload = _canonical_report_bytes(validated_report)
            artifact_name = REPORT_NAME
        else:
            assert report is not None
            assert report_payload is not None
            normalized = _validate_slice(
                stdout,
                manifest,
                report,
                manifest_payload=manifest_payload,
                report_payload=report_payload,
                expected_ruleset=(
                    "lectra-catalog-rules.v1" if mode == "catalog" else "lectra-slice-rules.v1"
                ),
                max_bytes=max_bytes,
            )
            canonical_payload = _canonical_slice_bytes(normalized, max_bytes=max_bytes)
            artifact_name = CATALOG_NAME if mode == "catalog" else SLICE_NAME
        if stderr:
            sys.stderr.buffer.write(stderr)
            sys.stderr.buffer.flush()
        return _publish_report(
            opened_output,
            canonical_payload,
            name=artifact_name,
            max_bytes=max_bytes,
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--mode", choices=("audit", "catalog", "slice"), default="audit")
    parser.add_argument("--audit-report", type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)
    try:
        report_path = run_qualifier(
            image=args.image,
            input_dir=args.input,
            output_dir=args.output,
            manifest_path=args.manifest,
            timeout_seconds=args.timeout_seconds,
            mode=args.mode,
            audit_report_path=args.audit_report,
        )
    except QualifierRunnerError as error:
        parser.error(str(error))
    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
