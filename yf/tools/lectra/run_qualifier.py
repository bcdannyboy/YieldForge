"""Run the untrusted Lectra qualifier and publish one validated passive report."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path

from pydantic import ValidationError

from yieldforge.datasets.lectra_audit import LectraAuditReport
from yieldforge.datasets.source_manifest import DatasetSourceManifest

EXPECTED_FILENAMES = frozenset({"constraints.gz", "parts.gz", "shapes.gz", "tasks.gz"})
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = PROJECT_ROOT / "datasets" / "sources" / "lectra-7030786-v1.1.json"
DEFAULT_IMAGE = "yieldforge-lectra-qualifier:7030786-v1.1"
REPORT_NAME = "lectra-audit.json"
MAX_REPORT_BYTES = 4 * 1024 * 1024
MAX_STDERR_BYTES = 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 15 * 60
_IMAGE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,254}$")


class QualifierRunnerError(RuntimeError):
    """Qualification or trusted publication failed closed."""


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


def _drain_pipe(pipe: object, capture: _LimitedCapture) -> None:
    try:
        while chunk := pipe.read(64 * 1024):  # type: ignore[attr-defined]
            capture.append(chunk)
    finally:
        pipe.close()  # type: ignore[attr-defined]


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

    if abort_reason is not None:
        try:
            on_abort()
        finally:
            _terminate_process(process)
    else:
        process.wait()

    for thread in threads:
        thread.join(timeout=2)
    if any(thread.is_alive() for thread in threads):
        _terminate_process(process)
        raise QualifierRunnerError("qualifier output pipes did not close")

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
    if path.is_symlink() or not path.is_file():
        raise QualifierRunnerError("manifest must be a regular file, not a link")
    try:
        manifest = DatasetSourceManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as error:
        raise QualifierRunnerError("manifest validation failed") from error
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


def _require_empty_output_dir(output_dir: Path) -> Path:
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise QualifierRunnerError("output must be a regular directory, not a link")
    resolved = output_dir.resolve(strict=True)
    if any(output_dir.iterdir()):
        raise QualifierRunnerError("output directory must be empty")
    return resolved


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _validate_report(
    payload: bytes,
    manifest: DatasetSourceManifest,
    *,
    max_bytes: int = MAX_REPORT_BYTES,
) -> LectraAuditReport:
    if not payload or len(payload) > max_bytes:
        raise QualifierRunnerError("qualifier report violates the size limit")
    try:
        text = payload.decode("utf-8", errors="strict")
        decoder = json.JSONDecoder(parse_constant=_reject_json_constant)
        decoded, end = decoder.raw_decode(text)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise QualifierRunnerError("qualifier did not emit valid finite UTF-8 JSON") from error
    if text[end:] not in {"", "\n"}:
        raise QualifierRunnerError("qualifier must emit exactly one JSON payload")
    try:
        report = LectraAuditReport.model_validate(decoded)
    except ValidationError as error:
        raise QualifierRunnerError("qualifier report schema validation failed") from error
    if report.dataset_id != manifest.dataset_id:
        raise QualifierRunnerError("qualifier report dataset identity does not match the manifest")
    expected_checksums = {source.name: source.checksum for source in manifest.files}
    if report.source_checksums != expected_checksums:
        raise QualifierRunnerError("qualifier report checksum identity does not match the manifest")
    return report


def _publish_report(output_dir: Path, payload: bytes) -> Path:
    """Atomically link a complete file into an empty directory without overwriting."""
    if len(payload) > MAX_REPORT_BYTES:
        raise QualifierRunnerError("qualifier report violates the size limit")
    resolved = _require_empty_output_dir(output_dir)
    destination = resolved / REPORT_NAME
    temporary = resolved / f".{REPORT_NAME}.{uuid.uuid4().hex}.tmp"
    temporary_created = False
    destination_created = False
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
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

        if {entry.name for entry in resolved.iterdir()} != {temporary.name}:
            raise QualifierRunnerError("unknown output appeared during publication")
        try:
            os.link(temporary, destination, follow_symlinks=False)
        except FileExistsError as error:
            raise QualifierRunnerError("refusing to overwrite an existing report") from error
        destination_created = True
        temporary.unlink()
        temporary_created = False

        directory_fd = os.open(resolved, os.O_RDONLY | os.O_CLOEXEC)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        entries = list(resolved.iterdir())
        if {entry.name for entry in entries} != {REPORT_NAME} or len(entries) != 1:
            raise QualifierRunnerError("published output failed its exact postcondition")
        metadata = destination.lstat()
        if destination.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise QualifierRunnerError("published report is not a regular file")
        if destination.read_bytes() != payload:
            raise QualifierRunnerError("published report bytes changed unexpectedly")
        return destination
    except BaseException:
        if temporary_created:
            temporary.unlink(missing_ok=True)
        if destination_created:
            destination.unlink(missing_ok=True)
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
) -> list[str]:
    if not _IMAGE_PATTERN.fullmatch(image) or image.startswith("-"):
        raise QualifierRunnerError("invalid qualifier image reference")
    return [
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
        "8g",
        "--memory-swap",
        "8g",
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


def _remove_container(container_name: str) -> None:
    try:
        subprocess.run(
            ["docker", "rm", "--force", container_name],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _stop_and_remove_container(container_name: str) -> None:
    try:
        subprocess.run(
            ["docker", "stop", "--time", "1", container_name],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
    _remove_container(container_name)


def run_qualifier(
    *,
    image: str,
    input_dir: Path,
    output_dir: Path,
    manifest_path: Path,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Path:
    manifest = _load_manifest(manifest_path)
    resolved_input = _validate_input_dir(input_dir, manifest)
    _require_empty_output_dir(output_dir)
    uid, gid = _nonroot_identity()
    container_name = f"yieldforge-lectra-{uuid.uuid4().hex}"
    command = _docker_command(
        image=image,
        container_name=container_name,
        input_dir=resolved_input,
        uid=uid,
        gid=gid,
    )
    try:
        return_code, stdout, stderr = _capture_process(
            command,
            timeout_seconds=timeout_seconds,
            stdout_limit=MAX_REPORT_BYTES,
            stderr_limit=MAX_STDERR_BYTES,
            on_abort=lambda: _stop_and_remove_container(container_name),
        )
    finally:
        _remove_container(container_name)

    if return_code != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise QualifierRunnerError(f"qualifier container failed with exit {return_code}: {detail}")
    _validate_report(stdout, manifest)
    if stderr:
        sys.stderr.buffer.write(stderr)
        sys.stderr.buffer.flush()
    _require_empty_output_dir(output_dir)
    return _publish_report(output_dir, stdout)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)
    try:
        report_path = run_qualifier(
            image=args.image,
            input_dir=args.input,
            output_dir=args.output,
            manifest_path=args.manifest,
            timeout_seconds=args.timeout_seconds,
        )
    except QualifierRunnerError as error:
        parser.error(str(error))
    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
