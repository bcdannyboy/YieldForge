"""Race-resistant publication of immutable M8 artifacts."""

from __future__ import annotations

import ctypes
import errno
import fcntl
import hashlib
import os
import secrets
import stat
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


class M8ArtifactPublicationError(ValueError):
    """Raised when an immutable artifact cannot be published safely."""

    def __init__(self, label: str, kind: str, detail: str) -> None:
        self.label = label
        self.kind = kind
        self.detail = detail
        super().__init__(f"{label} {detail}")


@dataclass(frozen=True, slots=True)
class _Identity:
    device: int
    inode: int


@dataclass(frozen=True, slots=True)
class _Directory:
    descriptor: int
    identity: _Identity
    name_from_parent: str | None


@dataclass(frozen=True, slots=True)
class _StableFile:
    data: bytes
    digest: bytes
    identity: _Identity
    size: int
    fingerprint: tuple[int, int, int, int, int]


@dataclass(slots=True)
class _ParentPublication:
    descriptor: int
    identity: _Identity
    held: bool = False


_DIRECTORY_OPEN_FLAGS = (
    os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
)
_FILE_READ_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
_STAGING_OPEN_FLAGS = (
    os.O_RDWR | os.O_CLOEXEC | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
)
_READ_SIZE = 1024 * 1024
_DARWIN_RENAME_EXCL = 0x00000004
_DARWIN_RENAME_NOFOLLOW_ANY = 0x00000010
_LINUX_RENAME_NOREPLACE = 0x00000001
_LIBC = ctypes.CDLL(None, use_errno=True)
_PUBLICATION_STATE = threading.local()
_VALIDATION_STATE = threading.local()


def _rename_no_replace(
    source: str,
    destination: str,
    *,
    src_dir_fd: int,
    dst_dir_fd: int,
) -> None:
    """Atomically move one directory entry without replacing its destination."""

    if sys.platform == "darwin":
        rename = _LIBC.renameatx_np
        flags = _DARWIN_RENAME_EXCL | _DARWIN_RENAME_NOFOLLOW_ANY
    elif sys.platform.startswith("linux"):
        try:
            rename = _LIBC.renameat2
        except AttributeError as exc:
            raise OSError(errno.ENOTSUP, "atomic no-replace rename is unavailable") from exc
        flags = _LINUX_RENAME_NOREPLACE
    else:
        raise OSError(errno.ENOTSUP, "atomic no-replace rename is unavailable")

    rename.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    rename.restype = ctypes.c_int
    result = rename(
        src_dir_fd,
        os.fsencode(source),
        dst_dir_fd,
        os.fsencode(destination),
        flags,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), destination)


def _error(
    label: str,
    kind: str,
    detail: str,
    cause: BaseException | None = None,
) -> M8ArtifactPublicationError:
    error = M8ArtifactPublicationError(label, kind, detail)
    if cause is not None:
        error.__cause__ = cause
    return error


def _identity(metadata: os.stat_result) -> _Identity:
    return _Identity(metadata.st_dev, metadata.st_ino)


def _same_identity(metadata: os.stat_result, expected: _Identity) -> bool:
    return _identity(metadata) == expected


def _stable_fingerprint(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _validate_exact(
    data: bytes,
    *,
    validate: Callable[[bytes], bytes],
    label: str,
) -> None:
    _enter_validator_callback()
    try:
        try:
            canonical = validate(data)
        except Exception as exc:
            raise _error(label, "validation", "validation failed", exc) from exc
    finally:
        _leave_validator_callback()
    if type(canonical) is not bytes or canonical != data:
        raise _error(label, "canonical", "canonical bytes differ")


def _enter_validator_callback() -> None:
    depth = getattr(_VALIDATION_STATE, "depth", 0)
    _VALIDATION_STATE.depth = depth + 1


def _leave_validator_callback() -> None:
    depth = getattr(_VALIDATION_STATE, "depth", 0)
    if depth <= 1:
        _VALIDATION_STATE.depth = 0
    else:
        _VALIDATION_STATE.depth = depth - 1


def _reject_publication_from_validator(*, label: str) -> None:
    if getattr(_VALIDATION_STATE, "depth", 0):
        raise _error(
            label,
            "parent",
            "publication from the active validator callback is forbidden",
        )


def _close_descriptor_once(descriptor: int) -> list[BaseException]:
    """Attempt one close and permanently relinquish the numeric descriptor.

    POSIX leaves descriptor state ambiguous when ``close`` reports an error. The
    number may already have been reused, even for the same inode with identical
    flags and offset, so retrying could close an unrelated open-file description.
    """

    try:
        os.close(descriptor)
        return []
    except BaseException as exc:
        return [exc]


def _propagate_close_errors(
    errors: list[BaseException],
    body_error: BaseException | None,
    *,
    subject: str,
) -> None:
    if not errors:
        return
    first_error = errors[0]
    for additional_error in errors[1:]:
        first_error.add_note(
            f"additional {subject} close failure: "
            f"{type(additional_error).__name__}: {additional_error}"
        )
    if body_error is None:
        raise first_error
    for close_error in errors:
        body_error.add_note(
            f"{subject} close failure: {type(close_error).__name__}: {close_error}"
        )


def _close_directories(directories: list[_Directory]) -> list[BaseException]:
    errors: list[BaseException] = []
    for directory in reversed(directories):
        errors.extend(_close_descriptor_once(directory.descriptor))
    return errors


def _open_verified_directory(
    name: str,
    *,
    dir_fd: int | None = None,
    label: str,
) -> tuple[int, os.stat_result]:
    """Open and type-check a directory while owning its descriptor immediately."""

    try:
        descriptor = os.open(name, _DIRECTORY_OPEN_FLAGS, dir_fd=dir_fd)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise _error(label, "parent", "parent directory traversal failed", exc) from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise _error(label, "parent", "parent entry is not a directory")
        return descriptor, metadata
    except BaseException as body_error:
        close_errors = _close_descriptor_once(descriptor)
        _propagate_close_errors(
            close_errors,
            body_error,
            subject="directory descriptor",
        )
        raise


def _open_parent_chain(absolute_path: Path, *, label: str) -> list[_Directory]:
    parent_parts = absolute_path.parent.parts
    if not parent_parts or parent_parts[0] != os.sep:
        raise _error(label, "parent", "parent path is not absolute")

    directories: list[_Directory] = []
    try:
        root_descriptor, root_metadata = _open_verified_directory(
            os.sep,
            label=label,
        )
        directories.append(
            _Directory(root_descriptor, _identity(root_metadata), None)
        )

        for component in parent_parts[1:]:
            parent_descriptor = directories[-1].descriptor
            try:
                descriptor, metadata = _open_verified_directory(
                    component,
                    dir_fd=parent_descriptor,
                    label=label,
                )
            except FileNotFoundError:
                try:
                    os.mkdir(component, 0o755, dir_fd=parent_descriptor)
                    os.fsync(parent_descriptor)
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise _error(
                        label,
                        "parent",
                        "parent directory creation or fsync failed",
                        exc,
                    ) from exc
                try:
                    descriptor, metadata = _open_verified_directory(
                        component,
                        dir_fd=parent_descriptor,
                        label=label,
                    )
                except M8ArtifactPublicationError as exc:
                    raise _error(
                        label,
                        "parent",
                        "parent directory traversal failed",
                        exc,
                    ) from exc
            except M8ArtifactPublicationError as exc:
                raise _error(
                    label,
                    "parent",
                    "parent directory traversal failed",
                    exc,
                ) from exc

            directories.append(
                _Directory(descriptor, _identity(metadata), component)
            )

        _recheck_parent_chain(directories, label=label)
        return directories
    except M8ArtifactPublicationError:
        _close_directories(directories)
        raise
    except OSError as exc:
        _close_directories(directories)
        raise _error(
            label,
            "parent",
            "parent directory traversal failed",
            exc,
        ) from exc
    except BaseException:
        _close_directories(directories)
        raise


def _recheck_parent_chain(
    directories: list[_Directory],
    *,
    label: str,
) -> None:
    for index, directory in enumerate(directories):
        try:
            current = os.fstat(directory.descriptor)
        except OSError as exc:
            raise _error(
                label,
                "parent_identity",
                "parent identity check failed",
                exc,
            ) from exc
        if not stat.S_ISDIR(current.st_mode) or not _same_identity(
            current, directory.identity
        ):
            raise _error(label, "parent_identity", "parent identity changed")
        if index == 0:
            try:
                root = os.stat(os.sep, follow_symlinks=False)
            except OSError as exc:
                raise _error(
                    label,
                    "parent_identity",
                    "ancestor root identity check failed",
                    exc,
                ) from exc
            if not stat.S_ISDIR(root.st_mode) or not _same_identity(
                root, directory.identity
            ):
                raise _error(
                    label,
                    "parent_identity",
                    "ancestor root identity changed",
                )
            continue

        parent = directories[index - 1]
        assert directory.name_from_parent is not None
        try:
            edge = os.stat(
                directory.name_from_parent,
                dir_fd=parent.descriptor,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise _error(
                label,
                "parent_identity",
                "ancestor parent identity changed",
                exc,
            ) from exc
        if not stat.S_ISDIR(edge.st_mode) or not _same_identity(
            edge, directory.identity
        ):
            raise _error(
                label,
                "parent_identity",
                "ancestor parent identity changed",
            )


def _read_all(descriptor: int, *, label: str, subject: str) -> bytes:
    chunks: list[bytes] = []
    while True:
        try:
            chunk = os.read(descriptor, _READ_SIZE)
        except InterruptedError:
            continue
        except OSError as exc:
            raise _error(label, "identity", f"{subject} read failed", exc) from exc
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _stat_optional(
    parent_descriptor: int,
    name: str,
    *,
    label: str,
    subject: str,
) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _error(label, "identity", f"{subject} identity check failed", exc) from exc


def _stable_read_named_file(
    parent_descriptor: int,
    name: str,
    *,
    label: str,
    subject: str,
) -> _StableFile | None:
    before_name = _stat_optional(
        parent_descriptor,
        name,
        label=label,
        subject=subject,
    )
    if before_name is None:
        return None
    if not stat.S_ISREG(before_name.st_mode):
        raise _error(label, "destination", f"{subject} destination is not regular")

    try:
        descriptor = os.open(name, _FILE_READ_FLAGS, dir_fd=parent_descriptor)
    except OSError as exc:
        raise _error(label, "identity", f"{subject} identity changed", exc) from exc
    try:
        before_file = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before_file.st_mode)
            or _identity(before_file) != _identity(before_name)
        ):
            raise _error(label, "identity", f"{subject} identity changed")
        data = _read_all(descriptor, label=label, subject=subject)
        after_file = os.fstat(descriptor)
    finally:
        body_error = sys.exception()
        close_errors = _close_descriptor_once(descriptor)
        _propagate_close_errors(
            close_errors,
            body_error,
            subject=f"{subject} read descriptor",
        )

    after_name = _stat_optional(
        parent_descriptor,
        name,
        label=label,
        subject=subject,
    )
    if after_name is None:
        raise _error(label, "identity", f"{subject} identity changed")
    if (
        not stat.S_ISREG(after_file.st_mode)
        or not stat.S_ISREG(after_name.st_mode)
        or _stable_fingerprint(before_file) != _stable_fingerprint(after_file)
        or _stable_fingerprint(after_file) != _stable_fingerprint(after_name)
        or after_file.st_size != len(data)
    ):
        raise _error(label, "identity", f"{subject} identity changed")

    return _StableFile(
        data=data,
        digest=hashlib.sha256(data).digest(),
        identity=_identity(after_file),
        size=len(data),
        fingerprint=_stable_fingerprint(after_file),
    )


def _fsync_exact_named_file(
    parent_descriptor: int,
    name: str,
    expected: _StableFile,
    *,
    label: str,
    subject: str,
) -> None:
    """Fsync the exact named inode and prove its name and metadata stayed bound."""

    before_name = _stat_optional(
        parent_descriptor,
        name,
        label=label,
        subject=subject,
    )
    if (
        before_name is None
        or not stat.S_ISREG(before_name.st_mode)
        or not _same_identity(before_name, expected.identity)
        or _stable_fingerprint(before_name) != expected.fingerprint
    ):
        raise _error(label, "identity", f"{subject} identity changed")

    try:
        descriptor = os.open(name, _FILE_READ_FLAGS, dir_fd=parent_descriptor)
    except OSError as exc:
        raise _error(label, "identity", f"{subject} identity changed", exc) from exc
    try:
        before_file = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before_file.st_mode)
            or not _same_identity(before_file, expected.identity)
            or _stable_fingerprint(before_file) != expected.fingerprint
            or _stable_fingerprint(before_file) != _stable_fingerprint(before_name)
        ):
            raise _error(label, "identity", f"{subject} identity changed")
        _fsync(descriptor, label=label, subject=f"{subject} file")
        after_file = os.fstat(descriptor)
    finally:
        body_error = sys.exception()
        close_errors = _close_descriptor_once(descriptor)
        _propagate_close_errors(
            close_errors,
            body_error,
            subject=f"{subject} fsync descriptor",
        )

    after_name = _stat_optional(
        parent_descriptor,
        name,
        label=label,
        subject=subject,
    )
    if (
        after_name is None
        or not stat.S_ISREG(after_file.st_mode)
        or not stat.S_ISREG(after_name.st_mode)
        or _stable_fingerprint(before_file) != _stable_fingerprint(after_file)
        or _stable_fingerprint(after_file) != _stable_fingerprint(after_name)
    ):
        raise _error(label, "identity", f"{subject} identity changed")


def _assert_named_identity(
    parent_descriptor: int,
    name: str,
    expected: _Identity,
    *,
    label: str,
    subject: str,
) -> None:
    current = _stat_optional(
        parent_descriptor,
        name,
        label=label,
        subject=subject,
    )
    if (
        current is None
        or not stat.S_ISREG(current.st_mode)
        or not _same_identity(current, expected)
    ):
        raise _error(label, "identity", f"{subject} identity changed")


def _write_all(descriptor: int, data: bytes, *, label: str) -> None:
    view = memoryview(data)
    written = 0
    while written < len(view):
        try:
            count = os.write(descriptor, view[written:])
        except InterruptedError:
            continue
        except OSError as exc:
            raise _error(label, "write", "staging write failed", exc) from exc
        if count <= 0:
            raise _error(label, "write", "staging write made no progress")
        written += count


def _create_staging_file(
    parent_descriptor: int,
    destination_name: str,
    *,
    label: str,
) -> tuple[str, int]:
    for _attempt in range(128):
        name = f".{destination_name}.tmp-{secrets.token_hex(16)}"
        try:
            descriptor = os.open(
                name,
                _STAGING_OPEN_FLAGS,
                0o600,
                dir_fd=parent_descriptor,
            )
        except FileExistsError:
            continue
        except OSError as exc:
            raise _error(label, "write", "staging creation failed", exc) from exc
        return name, descriptor
    raise _error(label, "write", "staging creation exhausted unique names")


def _bind_staging_file(
    parent_descriptor: int,
    name: str,
    descriptor: int,
    data: bytes,
    expected_digest: bytes,
    *,
    label: str,
) -> _StableFile:
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode) or before.st_size != len(data):
        raise _error(label, "identity", "staging identity or size changed")
    identity = _identity(before)
    _assert_named_identity(
        parent_descriptor,
        name,
        identity,
        label=label,
        subject="staging",
    )

    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
    except OSError as exc:
        raise _error(label, "identity", "staging readback failed", exc) from exc
    reread = _read_all(descriptor, label=label, subject="staging")
    after = os.fstat(descriptor)
    if (
        _stable_fingerprint(before) != _stable_fingerprint(after)
        or reread != data
        or hashlib.sha256(reread).digest() != expected_digest
    ):
        raise _error(label, "identity", "staging identity, size, or digest changed")
    _assert_named_identity(
        parent_descriptor,
        name,
        identity,
        label=label,
        subject="staging",
    )
    return _StableFile(
        data=reread,
        digest=hashlib.sha256(reread).digest(),
        identity=identity,
        size=len(reread),
        fingerprint=_stable_fingerprint(after),
    )


def _restore_quarantined_entry(
    parent_descriptor: int,
    quarantine_name: str,
    original_name: str,
    *,
    label: str,
    subject: str,
) -> None:
    try:
        _rename_no_replace(
            quarantine_name,
            original_name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
    except OSError as exc:
        raise _error(
            label,
            "identity",
            f"{subject} foreign entry could not be restored",
            exc,
        ) from exc


def _remove_owned_entry(
    parent_descriptor: int,
    name: str,
    expected: _Identity,
    *,
    label: str,
    subject: str,
) -> bool:
    """Remove an owned public name through an exclusive random quarantine move.

    The caller holds the parent-directory flock. The no-clobber move captures whichever
    inode owns the public name atomically, so a foreign replacement is restored before
    deletion. The unlinked quarantine name is random and never exposed as an authority.
    """

    current = _stat_optional(
        parent_descriptor,
        name,
        label=label,
        subject=subject,
    )
    if current is None or not stat.S_ISREG(current.st_mode) or not _same_identity(
        current,
        expected,
    ):
        return False

    quarantine_name: str | None = None
    quarantine_move_errors: list[BaseException] = []
    for _attempt in range(128):
        candidate = f".m8-publisher-cleanup-{secrets.token_hex(16)}"
        try:
            _rename_no_replace(
                name,
                candidate,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
        except BaseException as exc:
            candidate_state = _stat_optional(
                parent_descriptor,
                candidate,
                label=label,
                subject=subject,
            )
            original_state = _stat_optional(
                parent_descriptor,
                name,
                label=label,
                subject=subject,
            )
            candidate_is_owned = (
                candidate_state is not None
                and stat.S_ISREG(candidate_state.st_mode)
                and _same_identity(candidate_state, expected)
            )
            original_is_owned = (
                original_state is not None
                and stat.S_ISREG(original_state.st_mode)
                and _same_identity(original_state, expected)
            )
            if candidate_is_owned:
                quarantine_name = candidate
                quarantine_move_errors.append(exc)
                break
            if isinstance(exc, FileExistsError) and original_is_owned:
                continue
            if original_is_owned:
                quarantine_move_errors.append(exc)
                continue
            if isinstance(exc, FileNotFoundError) and original_state is None:
                return False
            if isinstance(exc, OSError):
                raise _error(
                    label,
                    "identity",
                    f"{subject} quarantine move failed",
                    exc,
                ) from exc
            raise
        quarantine_name = candidate
        break
    if quarantine_name is None:
        if quarantine_move_errors:
            first_move_error = quarantine_move_errors[0]
            for additional_error in quarantine_move_errors[1:]:
                first_move_error.add_note(
                    "additional quarantine move failure: "
                    f"{type(additional_error).__name__}: {additional_error}"
                )
            raise first_move_error
        raise _error(label, "identity", f"{subject} quarantine names exhausted")

    quarantined = _stat_optional(
        parent_descriptor,
        quarantine_name,
        label=label,
        subject=subject,
    )
    if (
        quarantined is None
        or not stat.S_ISREG(quarantined.st_mode)
        or not _same_identity(quarantined, expected)
    ):
        if quarantined is not None:
            _restore_quarantined_entry(
                parent_descriptor,
                quarantine_name,
                name,
                label=label,
                subject=subject,
            )
        return False

    try:
        os.unlink(quarantine_name, dir_fd=parent_descriptor)
    except OSError as exc:
        raise _error(label, "identity", f"{subject} quarantine cleanup failed", exc) from exc
    if quarantine_move_errors:
        first_move_error = quarantine_move_errors[0]
        if isinstance(first_move_error, OSError):
            reported_error: BaseException = _error(
                label,
                "identity",
                f"{subject} quarantine move reported failure after owned move",
                first_move_error,
            )
        else:
            reported_error = first_move_error
        for additional_error in quarantine_move_errors[1:]:
            reported_error.add_note(
                "additional quarantine move failure: "
                f"{type(additional_error).__name__}: {additional_error}"
            )
        raise reported_error
    return True


def _lock_parent_directory(descriptor: int, *, label: str) -> None:
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            return
        except InterruptedError:
            continue
        except OSError as exc:
            raise _error(label, "parent", "parent directory lock failed", exc) from exc


def _active_parent_identities() -> set[_Identity]:
    active = getattr(_PUBLICATION_STATE, "parent_identities", None)
    if active is None:
        active = set()
        _PUBLICATION_STATE.parent_identities = active
    return active


def _enter_parent_publication(
    publication: _ParentPublication,
    *,
    label: str,
) -> None:
    if publication.held:
        raise _error(label, "parent", "parent directory lock is already held")
    active = _active_parent_identities()
    if active:
        raise _error(label, "parent", "nested publication while validating is forbidden")
    publication.held = True
    try:
        active.add(publication.identity)
        _lock_parent_directory(publication.descriptor, label=label)
    except BaseException:
        active.discard(publication.identity)
        publication.held = False
        raise


def _acquire_parent_publication(
    publication: _ParentPublication,
    *,
    label: str,
) -> None:
    _enter_parent_publication(
        publication,
        label=label,
    )


def _release_parent_publication(
    publication: _ParentPublication,
    *,
    label: str,
) -> None:
    if not publication.held:
        return
    try:
        while True:
            try:
                fcntl.flock(publication.descriptor, fcntl.LOCK_UN)
                break
            except InterruptedError:
                continue
            except OSError as exc:
                raise _error(
                    label,
                    "parent",
                    "parent directory unlock failed",
                    exc,
                ) from exc
    finally:
        publication.held = False
        _active_parent_identities().discard(publication.identity)


def _fsync(
    descriptor: int,
    *,
    label: str,
    subject: str,
) -> None:
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise _error(label, "fsync", f"{subject} fsync failed", exc) from exc


def _verify_exact_artifact(
    artifact: _StableFile,
    data: bytes,
    expected_digest: bytes,
    *,
    label: str,
) -> None:
    if (
        artifact.size != len(data)
        or artifact.digest != expected_digest
        or artifact.data != data
    ):
        raise _error(label, "conflict", "immutable artifact differs")


def _assert_unchanged_artifact(
    parent_descriptor: int,
    name: str,
    expected: _StableFile,
    *,
    label: str,
    subject: str = "destination",
) -> None:
    current = _stable_read_named_file(
        parent_descriptor,
        name,
        label=label,
        subject=subject,
    )
    if (
        current is None
        or current.identity != expected.identity
        or current.size != expected.size
        or current.digest != expected.digest
        or current.data != expected.data
        or current.fingerprint != expected.fingerprint
    ):
        raise _error(label, "identity", f"{subject} identity or content changed")


def publish_immutable_artifact(
    path: Path,
    data: bytes,
    *,
    validate: Callable[[bytes], bytes],
    label: str,
) -> Path:
    """Publish canonical bytes exactly once without following filesystem links."""

    _reject_publication_from_validator(label=label)
    destination = Path(path)
    if type(data) is not bytes:
        raise _error(label, "validation", "validation requires exact bytes")
    absolute_path = Path(os.path.abspath(os.fspath(destination)))
    destination_name = absolute_path.name
    if not destination_name:
        raise _error(label, "destination", "destination path is not a file")
    _validate_exact(data, validate=validate, label=label)
    expected_digest = hashlib.sha256(data).digest()

    directories = _open_parent_chain(absolute_path, label=label)
    parent_descriptor = directories[-1].descriptor
    staging_name: str | None = None
    staging_cleanup_name: str | None = None
    staging_descriptor: int | None = None
    staging_identity: _Identity | None = None
    staging_artifact: _StableFile | None = None
    installed = False
    complete = False
    publication = _ParentPublication(
        descriptor=parent_descriptor,
        identity=directories[-1].identity,
    )

    try:
        _acquire_parent_publication(publication, label=label)
        _recheck_parent_chain(directories, label=label)
        existing = _stable_read_named_file(
            parent_descriptor,
            destination_name,
            label=label,
            subject="destination",
        )
        if existing is not None:
            _verify_exact_artifact(
                existing,
                data,
                expected_digest,
                label=label,
            )
            _fsync_exact_named_file(
                parent_descriptor,
                destination_name,
                existing,
                label=label,
                subject="destination",
            )
            _recheck_parent_chain(directories, label=label)
            _assert_unchanged_artifact(
                parent_descriptor,
                destination_name,
                existing,
                label=label,
            )
            _fsync(parent_descriptor, label=label, subject="parent directory")
            _recheck_parent_chain(directories, label=label)
            _assert_unchanged_artifact(
                parent_descriptor,
                destination_name,
                existing,
                label=label,
            )
            _recheck_parent_chain(directories, label=label)
            complete = True
            return destination

        staging_name, staging_descriptor = _create_staging_file(
            parent_descriptor,
            destination_name,
            label=label,
        )
        staging_cleanup_name = staging_name
        try:
            try:
                staging_metadata = os.fstat(staging_descriptor)
            except BaseException as exc:
                try:
                    recovery_metadata = os.fstat(staging_descriptor)
                except OSError:
                    pass
                else:
                    if stat.S_ISREG(recovery_metadata.st_mode):
                        staging_identity = _identity(recovery_metadata)
                if isinstance(exc, OSError):
                    raise _error(
                        label,
                        "identity",
                        "staging metadata check failed",
                        exc,
                    ) from exc
                raise
            if not stat.S_ISREG(staging_metadata.st_mode):
                staging_identity = _identity(staging_metadata)
                raise _error(label, "identity", "staging entry is not regular")
            staging_identity = _identity(staging_metadata)
            _write_all(staging_descriptor, data, label=label)
            before_sync = _bind_staging_file(
                parent_descriptor,
                staging_name,
                staging_descriptor,
                data,
                expected_digest,
                label=label,
            )
            _fsync(staging_descriptor, label=label, subject="staging file")
            after_sync = _bind_staging_file(
                parent_descriptor,
                staging_name,
                staging_descriptor,
                data,
                expected_digest,
                label=label,
            )
            if before_sync != after_sync or after_sync.identity != staging_identity:
                raise _error(label, "identity", "staging changed across file fsync")
            staging_artifact = after_sync
        finally:
            staging_body_error = sys.exception()
            assert staging_descriptor is not None
            staging_close_errors = _close_descriptor_once(staging_descriptor)
            staging_descriptor = None
            if staging_close_errors:
                first_close_error = staging_close_errors[0]
                for additional_error in staging_close_errors[1:]:
                    first_close_error.add_note(
                        "additional staging descriptor close failure: "
                        f"{type(additional_error).__name__}: {additional_error}"
                    )
                if staging_body_error is None:
                    raise first_close_error
                staging_body_error.add_note(
                    "staging descriptor close failure: "
                    f"{type(first_close_error).__name__}: {first_close_error}"
                )

        assert staging_artifact is not None
        _recheck_parent_chain(directories, label=label)
        _assert_unchanged_artifact(
            parent_descriptor,
            staging_name,
            staging_artifact,
            label=label,
            subject="staging",
        )

        _recheck_parent_chain(directories, label=label)
        try:
            _rename_no_replace(
                staging_name,
                destination_name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
            installed = True
            staging_name = None
        except BaseException as exc:
            assert staging_identity is not None
            current_stage = _stat_optional(
                parent_descriptor,
                staging_cleanup_name,
                label=label,
                subject="staging",
            )
            current_destination = _stat_optional(
                parent_descriptor,
                destination_name,
                label=label,
                subject="destination",
            )
            destination_is_owned = (
                current_destination is not None
                and stat.S_ISREG(current_destination.st_mode)
                and _same_identity(current_destination, staging_identity)
            )
            stage_is_owned = (
                current_stage is not None
                and stat.S_ISREG(current_stage.st_mode)
                and _same_identity(current_stage, staging_identity)
            )
            if destination_is_owned:
                installed = True
                if current_stage is None:
                    staging_name = None
                if isinstance(exc, OSError):
                    raise _error(
                        label,
                        "install",
                        "install reported failure after destination appeared",
                        exc,
                    ) from exc
                raise
            if not stage_is_owned:
                if isinstance(exc, OSError):
                    raise _error(
                        label,
                        "identity",
                        "staging identity changed during install",
                        exc,
                    ) from exc
                raise
            if isinstance(exc, FileExistsError):
                raced = _stable_read_named_file(
                    parent_descriptor,
                    destination_name,
                    label=label,
                    subject="destination",
                )
                if raced is None:
                    raise _error(
                        label,
                        "install",
                        "install race lost destination",
                        exc,
                    ) from exc
                _verify_exact_artifact(
                    raced,
                    data,
                    expected_digest,
                    label=label,
                )
                _fsync_exact_named_file(
                    parent_descriptor,
                    destination_name,
                    raced,
                    label=label,
                    subject="destination",
                )
                _recheck_parent_chain(directories, label=label)
                _assert_unchanged_artifact(
                    parent_descriptor,
                    destination_name,
                    raced,
                    label=label,
                )
                _recheck_parent_chain(directories, label=label)
            elif isinstance(exc, OSError):
                raise _error(label, "install", "install failed", exc) from exc
            else:
                raise

        assert staging_identity is not None
        if installed:
            assert staging_artifact is not None
            _recheck_parent_chain(directories, label=label)
            installed_baseline = _stable_read_named_file(
                parent_descriptor,
                destination_name,
                label=label,
                subject="destination",
            )
            if (
                installed_baseline is None
                or installed_baseline.identity != staging_identity
            ):
                raise _error(label, "identity", "destination identity changed")
            _verify_exact_artifact(
                installed_baseline,
                data,
                expected_digest,
                label=label,
            )
            _fsync_exact_named_file(
                parent_descriptor,
                destination_name,
                installed_baseline,
                label=label,
                subject="destination",
            )
            _recheck_parent_chain(directories, label=label)
            _assert_unchanged_artifact(
                parent_descriptor,
                destination_name,
                installed_baseline,
                label=label,
            )
            _fsync(parent_descriptor, label=label, subject="parent directory")
            final = _stable_read_named_file(
                parent_descriptor,
                destination_name,
                label=label,
                subject="destination",
            )
            if final is None or final != installed_baseline:
                raise _error(
                    label,
                    "identity",
                    "destination changed after installed file fsync",
                )
            _verify_exact_artifact(
                final,
                data,
                expected_digest,
                label=label,
            )
            _recheck_parent_chain(directories, label=label)
            _assert_unchanged_artifact(
                parent_descriptor,
                destination_name,
                final,
                label=label,
            )
            _recheck_parent_chain(directories, label=label)

        if not installed:
            assert staging_name is not None
            if not _remove_owned_entry(
                parent_descriptor,
                staging_name,
                staging_identity,
                label=label,
                subject="staging",
            ):
                raise _error(label, "identity", "staging identity changed during cleanup")
            staging_name = None
            _fsync(parent_descriptor, label=label, subject="parent directory")
        _recheck_parent_chain(directories, label=label)
        accepted = final if installed else raced
        _assert_unchanged_artifact(
            parent_descriptor,
            destination_name,
            accepted,
            label=label,
        )
        _recheck_parent_chain(directories, label=label)
        complete = True
        return destination
    except OSError as exc:
        raise _error(
            label,
            "integrity",
            "publication integrity check failed",
            exc,
        ) from exc
    finally:
        body_error = sys.exception()
        cleanup_errors: list[BaseException] = []
        cleanup_attempted = False
        if staging_descriptor is not None:
            staging_close_errors = _close_descriptor_once(staging_descriptor)
            cleanup_errors.extend(staging_close_errors)
            staging_descriptor = None
        if not complete and staging_identity is not None:
            cleanup_targets = [(destination_name, "destination")]
            if staging_cleanup_name is not None:
                cleanup_targets.append((staging_cleanup_name, "staging"))
            for cleanup_name, cleanup_subject in cleanup_targets:
                cleanup_attempted = True
                try:
                    _remove_owned_entry(
                        parent_descriptor,
                        cleanup_name,
                        staging_identity,
                        label=label,
                        subject=cleanup_subject,
                    )
                except BaseException as cleanup_error:
                    cleanup_errors.append(cleanup_error)
        if cleanup_attempted:
            try:
                os.fsync(parent_descriptor)
            except BaseException as exc:
                if isinstance(exc, OSError):
                    cleanup_errors.append(
                        _error(
                            label,
                            "fsync",
                            "rollback parent directory fsync failed",
                            exc,
                        )
                    )
                else:
                    cleanup_errors.append(exc)
        if publication.held:
            try:
                _release_parent_publication(publication, label=label)
            except BaseException as unlock_error:
                cleanup_errors.append(unlock_error)
        cleanup_errors.extend(_close_directories(directories))
        if cleanup_errors:
            first_cleanup_error = cleanup_errors[0]
            if body_error is None:
                for additional_error in cleanup_errors[1:]:
                    first_cleanup_error.add_note(
                        f"additional cleanup failure: {type(additional_error).__name__}: "
                        f"{additional_error}"
                    )
                raise first_cleanup_error
            for cleanup_error in cleanup_errors:
                body_error.add_note(
                    f"publication cleanup failure: {type(cleanup_error).__name__}: "
                    f"{cleanup_error}"
                )
