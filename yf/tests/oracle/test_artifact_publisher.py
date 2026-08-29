from __future__ import annotations

import fcntl
import json
import os
import stat
import threading
from pathlib import Path

import pytest


def _canonical(value: int) -> bytes:
    return (
        json.dumps({"value": value}, allow_nan=False, indent=2, sort_keys=True) + "\n"
    ).encode()


def _strict_load(data: bytes) -> dict[str, int]:
    parsed = json.loads(data)
    if (
        type(parsed) is not dict
        or set(parsed) != {"value"}
        or type(parsed["value"]) is not int
    ):
        raise TypeError("artifact payload differs")
    return parsed


def _validate(data: bytes) -> bytes:
    parsed = _strict_load(data)
    return _canonical(parsed["value"])


def _publish(path: Path, data: bytes) -> Path:
    from yieldforge.oracle.artifact_publisher import publish_immutable_artifact

    return publish_immutable_artifact(
        path,
        data,
        validate=_validate,
        label="test artifact",
    )


def _write_relative_file(
    parent_descriptor: int,
    name: str,
    data: bytes,
) -> None:
    descriptor = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
        dir_fd=parent_descriptor,
    )
    try:
        remaining = memoryview(data)
        while remaining:
            written = os.write(descriptor, remaining)
            assert written > 0
            remaining = remaining[written:]
    finally:
        os.close(descriptor)


def test_publisher_exposes_the_approved_error_contract() -> None:
    from yieldforge.oracle.artifact_publisher import M8ArtifactPublicationError

    assert issubclass(M8ArtifactPublicationError, ValueError)


@pytest.mark.parametrize(
    "data,validate",
    [
        (b'{"value": 1}\n', _validate),
        (_canonical(1), lambda _data: (_ for _ in ()).throw(TypeError("invalid"))),
        (_canonical(1), lambda _data: b"not canonical\n"),
    ],
)
def test_publisher_rejects_invalid_or_noncanonical_bytes_before_writes(
    tmp_path: Path,
    data: bytes,
    validate,  # type: ignore[no-untyped-def]
) -> None:
    from yieldforge.oracle.artifact_publisher import (
        M8ArtifactPublicationError,
        publish_immutable_artifact,
    )

    output = tmp_path / "absent" / "artifact.json"
    with pytest.raises(M8ArtifactPublicationError, match="test artifact.*canonical|validation"):
        publish_immutable_artifact(
            output,
            data,
            validate=validate,
            label="test artifact",
        )

    assert not output.parent.exists()


def test_publisher_creates_missing_parents_only_after_validation(tmp_path: Path) -> None:
    output = tmp_path / "new" / "nested" / "artifact.json"

    assert _publish(output, _canonical(1)) == output
    assert output.read_bytes() == _canonical(1)
    assert stat.S_ISREG(output.stat().st_mode)


def test_relative_destination_is_frozen_before_validator_changes_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle.artifact_publisher import publish_immutable_artifact

    parent_a = tmp_path / "a"
    parent_b = tmp_path / "b"
    parent_a.mkdir()
    parent_b.mkdir()
    destination = Path("artifact.json")
    data = _canonical(1)
    monkeypatch.chdir(parent_a)

    def change_cwd(value: bytes) -> bytes:
        os.chdir(parent_b)
        return _validate(value)

    assert publish_immutable_artifact(
        destination,
        data,
        validate=change_cwd,
        label="relative artifact",
    ) == destination
    assert (parent_a / destination).read_bytes() == data
    assert not (parent_b / destination).exists()


def test_publisher_is_exact_byte_idempotent_and_conflict_safe(tmp_path: Path) -> None:
    from yieldforge.oracle.artifact_publisher import M8ArtifactPublicationError

    output = tmp_path / "artifact.json"
    assert _publish(output, _canonical(1)) == output
    original = output.read_bytes()
    identity = output.stat().st_dev, output.stat().st_ino

    assert _publish(output, _canonical(1)) == output
    assert (output.stat().st_dev, output.stat().st_ino) == identity
    with pytest.raises(M8ArtifactPublicationError, match="immutable.*differs"):
        _publish(output, _canonical(2))

    assert output.read_bytes() == original


def test_idempotent_existing_publish_fsyncs_file_before_parent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher

    output = tmp_path / "artifact.json"
    data = _canonical(1)
    output.write_bytes(data)
    original_fsync = artifact_publisher.os.fsync
    calls: list[str] = []

    def record_fsync(descriptor: int) -> None:
        calls.append(
            "parent" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file"
        )
        original_fsync(descriptor)

    monkeypatch.setattr(artifact_publisher.os, "fsync", record_fsync)

    assert _publish(output, data) == output
    assert calls == ["file", "parent"]


def test_idempotent_existing_file_fsync_failure_is_typed_and_preserves_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher
    from yieldforge.oracle.artifact_publisher import M8ArtifactPublicationError

    output = tmp_path / "artifact.json"
    data = _canonical(1)
    output.write_bytes(data)
    original_fsync = artifact_publisher.os.fsync

    def fail_file_fsync(descriptor: int) -> None:
        if stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("forced accepted-file fsync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(artifact_publisher.os, "fsync", fail_file_fsync)

    with pytest.raises(M8ArtifactPublicationError, match="fsync") as failure:
        _publish(output, data)

    assert failure.value.kind == "fsync"
    assert output.read_bytes() == data


def test_idempotent_existing_publish_rejects_replacement_during_parent_fsync(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher
    from yieldforge.oracle.artifact_publisher import M8ArtifactPublicationError

    output = tmp_path / "artifact.json"
    expected = _canonical(1)
    foreign = _canonical(2)
    assert _publish(output, expected) == output
    original_fsync = artifact_publisher.os.fsync
    replaced = False

    def replace_during_parent_fsync(descriptor: int) -> None:
        nonlocal replaced
        if stat.S_ISDIR(os.fstat(descriptor).st_mode) and not replaced:
            replaced = True
            output.unlink()
            output.write_bytes(foreign)
        original_fsync(descriptor)

    monkeypatch.setattr(artifact_publisher.os, "fsync", replace_during_parent_fsync)

    with pytest.raises(M8ArtifactPublicationError, match="identity"):
        _publish(output, expected)

    assert replaced
    assert output.read_bytes() == foreign


def test_publisher_rejects_symlinked_parent_and_target(tmp_path: Path) -> None:
    from yieldforge.oracle.artifact_publisher import M8ArtifactPublicationError

    real_parent = tmp_path / "real"
    real_parent.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(M8ArtifactPublicationError, match="parent"):
        _publish(alias / "artifact.json", _canonical(1))
    assert tuple(real_parent.iterdir()) == ()

    target = tmp_path / "target.json"
    target.symlink_to(real_parent / "elsewhere.json")
    with pytest.raises(M8ArtifactPublicationError, match="destination"):
        _publish(target, _canonical(1))
    assert target.is_symlink()


@pytest.mark.parametrize("entry_kind", ["directory", "fifo"])
def test_publisher_rejects_nonregular_destination_entries(
    tmp_path: Path,
    entry_kind: str,
) -> None:
    from yieldforge.oracle.artifact_publisher import M8ArtifactPublicationError

    output = tmp_path / "artifact.json"
    if entry_kind == "directory":
        output.mkdir()
    else:
        os.mkfifo(output)

    with pytest.raises(M8ArtifactPublicationError, match="destination"):
        _publish(output, _canonical(1))


def test_publisher_completes_short_writes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from yieldforge.oracle import artifact_publisher

    original_write = artifact_publisher.os.write
    calls = 0

    def short_write(descriptor: int, data: object) -> int:
        nonlocal calls
        calls += 1
        return original_write(descriptor, memoryview(data)[:3])

    monkeypatch.setattr(artifact_publisher.os, "write", short_write)
    output = tmp_path / "artifact.json"

    assert _publish(output, _canonical(123456)) == output
    assert output.read_bytes() == _canonical(123456)
    assert calls > 1


def test_publisher_rejects_zero_progress_write_and_cleans_owned_stage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher
    from yieldforge.oracle.artifact_publisher import M8ArtifactPublicationError

    monkeypatch.setattr(artifact_publisher.os, "write", lambda _fd, _data: 0)
    output = tmp_path / "artifact.json"

    with pytest.raises(M8ArtifactPublicationError, match="write"):
        _publish(output, _canonical(1))

    assert tuple(tmp_path.iterdir()) == ()


def test_publisher_rejects_file_fsync_failure_without_installing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher
    from yieldforge.oracle.artifact_publisher import M8ArtifactPublicationError

    original_fsync = artifact_publisher.os.fsync

    def fail_file_fsync(descriptor: int) -> None:
        if stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("forced file fsync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(artifact_publisher.os, "fsync", fail_file_fsync)
    output = tmp_path / "artifact.json"

    with pytest.raises(M8ArtifactPublicationError, match="fsync"):
        _publish(output, _canonical(1))

    assert tuple(tmp_path.iterdir()) == ()


def test_publisher_rejects_parent_fsync_failure_and_rolls_back_owned_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher
    from yieldforge.oracle.artifact_publisher import M8ArtifactPublicationError

    original_fsync = artifact_publisher.os.fsync
    failed = False

    def fail_first_parent_fsync(descriptor: int) -> None:
        nonlocal failed
        if stat.S_ISDIR(os.fstat(descriptor).st_mode) and output.exists() and not failed:
            failed = True
            raise OSError("forced parent fsync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(artifact_publisher.os, "fsync", fail_first_parent_fsync)
    output = tmp_path / "artifact.json"

    with pytest.raises(M8ArtifactPublicationError, match="fsync"):
        _publish(output, _canonical(1))

    assert failed
    assert tuple(tmp_path.iterdir()) == ()


def test_publisher_rejects_final_inode_replacement_even_with_identical_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher
    from yieldforge.oracle.artifact_publisher import M8ArtifactPublicationError

    output = tmp_path / "artifact.json"
    data = _canonical(1)
    original_fsync = artifact_publisher.os.fsync
    replaced = False

    def replace_after_install(descriptor: int) -> None:
        nonlocal replaced
        if stat.S_ISDIR(os.fstat(descriptor).st_mode) and output.exists() and not replaced:
            replaced = True
            output.unlink()
            output.write_bytes(data)
        original_fsync(descriptor)

    monkeypatch.setattr(artifact_publisher.os, "fsync", replace_after_install)

    with pytest.raises(M8ArtifactPublicationError) as captured:
        _publish(output, data)

    assert captured.value.kind == "identity"
    assert replaced
    assert output.read_bytes() == data
    assert not tuple(tmp_path.glob(".artifact.json.tmp-*"))


def test_publisher_rejects_temporary_replacement_and_preserves_foreign_entry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher
    from yieldforge.oracle.artifact_publisher import M8ArtifactPublicationError

    original_rename = artifact_publisher._rename_no_replace
    foreign = b"foreign staging entry\n"

    def replace_then_fail_install(
        source: str,
        destination: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
    ) -> None:
        del destination, dst_dir_fd
        artifact_publisher.os.unlink(source, dir_fd=src_dir_fd)
        replacement = artifact_publisher.os.open(
            source,
            artifact_publisher.os.O_WRONLY
            | artifact_publisher.os.O_CREAT
            | artifact_publisher.os.O_EXCL,
            0o600,
            dir_fd=src_dir_fd,
        )
        try:
            original_write = os.write
            original_write(replacement, foreign)
        finally:
            artifact_publisher.os.close(replacement)
        raise OSError("forced install failure after replacement")

    monkeypatch.setattr(
        artifact_publisher,
        "_rename_no_replace",
        replace_then_fail_install,
    )
    output = tmp_path / "artifact.json"
    with pytest.raises(M8ArtifactPublicationError, match="install|identity"):
        _publish(output, _canonical(1))

    assert not output.exists()
    stages = tuple(tmp_path.glob(".artifact.json.tmp-*"))
    assert len(stages) == 1
    assert stages[0].read_bytes() == foreign
    assert original_rename is not None


def test_publisher_rejects_different_byte_final_race_without_overwrite(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher
    from yieldforge.oracle.artifact_publisher import M8ArtifactPublicationError

    output = tmp_path / "artifact.json"
    competing = _canonical(2)

    def install_competitor(
        source: str,
        destination: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
    ) -> None:
        if destination != output.name:
            original_rename(
                source,
                destination,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
            )
            return
        descriptor = artifact_publisher.os.open(
            destination,
            artifact_publisher.os.O_WRONLY
            | artifact_publisher.os.O_CREAT
            | artifact_publisher.os.O_EXCL,
            0o600,
            dir_fd=dst_dir_fd,
        )
        try:
            os.write(descriptor, competing)
        finally:
            artifact_publisher.os.close(descriptor)
        raise FileExistsError(destination)

    original_rename = artifact_publisher._rename_no_replace
    monkeypatch.setattr(artifact_publisher, "_rename_no_replace", install_competitor)
    with pytest.raises(M8ArtifactPublicationError, match="immutable.*differs"):
        _publish(output, _canonical(1))

    assert output.read_bytes() == competing
    assert not tuple(tmp_path.glob(".artifact.json.tmp-*"))


def test_same_byte_install_race_fsyncs_competitor_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher

    output = tmp_path / "artifact.json"
    data = _canonical(1)
    original_rename = artifact_publisher._rename_no_replace
    original_fsync = artifact_publisher.os.fsync
    accepted_file_fsyncs = 0

    def install_competitor(
        _source: str,
        destination: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
    ) -> None:
        if destination != output.name:
            original_rename(
                _source,
                destination,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
            )
            return
        _write_relative_file(dst_dir_fd, destination, data)
        raise FileExistsError(destination)

    def record_fsync(descriptor: int) -> None:
        nonlocal accepted_file_fsyncs
        metadata = os.fstat(descriptor)
        if output.exists() and (metadata.st_dev, metadata.st_ino) == (
            output.stat().st_dev,
            output.stat().st_ino,
        ):
            accepted_file_fsyncs += 1
        original_fsync(descriptor)

    monkeypatch.setattr(artifact_publisher, "_rename_no_replace", install_competitor)
    monkeypatch.setattr(artifact_publisher.os, "fsync", record_fsync)

    assert _publish(output, data) == output
    assert accepted_file_fsyncs == 1
    assert output.read_bytes() == data
    assert not tuple(tmp_path.glob(".artifact.json.tmp-*"))


def test_same_byte_install_race_file_fsync_failure_is_typed_and_preserves_competitor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher
    from yieldforge.oracle.artifact_publisher import M8ArtifactPublicationError

    output = tmp_path / "artifact.json"
    data = _canonical(1)
    original_rename = artifact_publisher._rename_no_replace
    original_fsync = artifact_publisher.os.fsync

    def install_competitor(
        _source: str,
        destination: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
    ) -> None:
        if destination != output.name:
            original_rename(
                _source,
                destination,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
            )
            return
        _write_relative_file(dst_dir_fd, destination, data)
        raise FileExistsError(destination)

    def fail_competitor_fsync(descriptor: int) -> None:
        metadata = os.fstat(descriptor)
        if output.exists() and (metadata.st_dev, metadata.st_ino) == (
            output.stat().st_dev,
            output.stat().st_ino,
        ):
            raise OSError("forced competitor fsync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(artifact_publisher, "_rename_no_replace", install_competitor)
    monkeypatch.setattr(artifact_publisher.os, "fsync", fail_competitor_fsync)

    with pytest.raises(M8ArtifactPublicationError, match="fsync") as failure:
        _publish(output, data)

    assert failure.value.kind == "fsync"
    assert output.read_bytes() == data
    assert not tuple(tmp_path.glob(".artifact.json.tmp-*"))


def test_validator_runs_once_before_any_publication_state(tmp_path: Path) -> None:
    from yieldforge.oracle.artifact_publisher import publish_immutable_artifact

    data = _canonical(1)
    output = tmp_path / "artifact.json"
    validation_calls = 0

    def validate(value: bytes) -> bytes:
        nonlocal validation_calls
        validation_calls += 1
        if validation_calls > 1:
            raise TypeError("validator was reentered after publication state began")
        return _validate(value)

    assert publish_immutable_artifact(
        output,
        data,
        validate=validate,
        label="test artifact",
    ) == output

    assert validation_calls == 1
    assert output.read_bytes() == data
    assert not tuple(tmp_path.glob(".artifact.json.tmp-*"))


def test_publisher_rejects_ancestor_replacement_while_using_retained_directory_fds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher
    from yieldforge.oracle.artifact_publisher import M8ArtifactPublicationError

    ancestor = tmp_path / "ancestor"
    parent = ancestor / "parent"
    parent.mkdir(parents=True)
    displaced = tmp_path / "ancestor-displaced"
    output = parent / "artifact.json"
    original_rename = artifact_publisher._rename_no_replace
    replaced = False

    def replace_ancestor_then_install(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal replaced
        if not replaced:
            ancestor.rename(displaced)
            parent.mkdir(parents=True)
            replaced = True
        return original_rename(*args, **kwargs)

    monkeypatch.setattr(
        artifact_publisher,
        "_rename_no_replace",
        replace_ancestor_then_install,
    )
    with pytest.raises(M8ArtifactPublicationError, match="parent.*identity|ancestor"):
        _publish(output, _canonical(1))

    assert replaced
    assert not output.exists()
    assert not (displaced / "parent" / "artifact.json").exists()


@pytest.mark.parametrize("replace_destination", [False, True], ids=["owned", "foreign"])
def test_publisher_reconciles_destination_when_install_moves_then_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    replace_destination: bool,
) -> None:
    from yieldforge.oracle import artifact_publisher
    from yieldforge.oracle.artifact_publisher import M8ArtifactPublicationError

    output = tmp_path / "artifact.json"
    foreign = b"foreign destination\n"
    original_rename = artifact_publisher._rename_no_replace
    original_unlink = artifact_publisher.os.unlink

    def install_then_raise(
        source: str,
        destination: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
    ) -> None:
        if destination != output.name:
            original_rename(
                source,
                destination,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
            )
            return
        original_rename(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        if replace_destination:
            original_unlink(destination, dir_fd=dst_dir_fd)
            _write_relative_file(dst_dir_fd, destination, foreign)
        raise OSError("forced uncertain install outcome")

    monkeypatch.setattr(artifact_publisher, "_rename_no_replace", install_then_raise)

    with pytest.raises(M8ArtifactPublicationError, match="install|integrity"):
        _publish(output, _canonical(1))

    if replace_destination:
        assert output.read_bytes() == foreign
    else:
        assert not output.exists()
    assert not tuple(tmp_path.glob(".artifact.json.tmp-*"))


def test_ambiguous_install_rolls_back_owned_final_and_preserves_foreign_stage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher
    from yieldforge.oracle.artifact_publisher import M8ArtifactPublicationError

    output = tmp_path / "artifact.json"
    foreign = b"foreign staging replacement\n"
    original_rename = artifact_publisher._rename_no_replace

    def install_replace_stage_then_raise(
        source: str,
        destination: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
    ) -> None:
        if destination != output.name:
            original_rename(
                source,
                destination,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
            )
            return
        original_rename(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        _write_relative_file(src_dir_fd, source, foreign)
        raise OSError("forced uncertain install with reused stage name")

    monkeypatch.setattr(
        artifact_publisher,
        "_rename_no_replace",
        install_replace_stage_then_raise,
    )

    with pytest.raises(M8ArtifactPublicationError, match="install|identity"):
        _publish(output, _canonical(1))

    assert not output.exists()
    stages = tuple(tmp_path.glob(".artifact.json.tmp-*"))
    assert len(stages) == 1
    assert stages[0].read_bytes() == foreign


def test_file_exists_after_install_side_effect_rolls_back_owned_final(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher
    from yieldforge.oracle.artifact_publisher import M8ArtifactPublicationError

    output = tmp_path / "artifact.json"
    original_rename = artifact_publisher._rename_no_replace

    def install_then_report_exists(
        source: str,
        destination: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
    ) -> None:
        if destination != output.name:
            original_rename(
                source,
                destination,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
            )
            return
        original_rename(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        raise FileExistsError(destination)

    monkeypatch.setattr(
        artifact_publisher,
        "_rename_no_replace",
        install_then_report_exists,
    )

    with pytest.raises(M8ArtifactPublicationError, match="install|identity"):
        _publish(output, _canonical(1))

    assert not output.exists()
    assert not tuple(tmp_path.glob(".artifact.json.tmp-*"))


def test_concurrent_same_byte_success_survives_first_publisher_fsync_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher
    from yieldforge.oracle.artifact_publisher import (
        M8ArtifactPublicationError,
        publish_immutable_artifact,
    )

    output = tmp_path / "artifact.json"
    data = _canonical(1)
    original_fsync = artifact_publisher.os.fsync
    first_installed = threading.Event()
    release_first = threading.Event()
    second_prevalidated = threading.Event()
    second_done = threading.Event()
    first_parent_fsync_failed = False
    results: dict[str, Path] = {}
    errors: dict[str, BaseException] = {}

    def controlled_fsync(descriptor: int) -> None:
        nonlocal first_parent_fsync_failed
        if (
            threading.current_thread().name == "first-publisher"
            and stat.S_ISDIR(os.fstat(descriptor).st_mode)
            and not first_parent_fsync_failed
        ):
            first_parent_fsync_failed = True
            first_installed.set()
            if not release_first.wait(timeout=5):
                raise RuntimeError("concurrent publisher coordination timed out")
            raise OSError("forced first publisher parent fsync failure")
        original_fsync(descriptor)

    def validate_second(value: bytes) -> bytes:
        second_prevalidated.set()
        return _validate(value)

    def run_publisher(name: str) -> None:
        try:
            results[name] = publish_immutable_artifact(
                output,
                data,
                validate=validate_second if name == "second" else _validate,
                label=f"{name} test artifact",
            )
        except BaseException as error:
            errors[name] = error
        finally:
            if name == "second":
                second_done.set()

    monkeypatch.setattr(artifact_publisher.os, "fsync", controlled_fsync)
    first = threading.Thread(target=run_publisher, args=("first",), name="first-publisher")
    second = threading.Thread(target=run_publisher, args=("second",), name="second-publisher")
    try:
        first.start()
        assert first_installed.wait(timeout=5)
        second.start()
        assert second_prevalidated.wait(timeout=5)
        second_done.wait(timeout=0.5)
    finally:
        release_first.set()
        first.join(timeout=5)
        second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert isinstance(errors.get("first"), M8ArtifactPublicationError)
    assert "second" not in errors
    assert results.get("second") == output
    assert output.exists()
    assert output.read_bytes() == data
    assert not tuple(tmp_path.glob(".artifact.json.tmp-*"))


def test_publisher_restores_foreign_stage_moved_inside_cleanup_seam(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher
    from yieldforge.oracle.artifact_publisher import M8ArtifactPublicationError

    output = tmp_path / "artifact.json"
    data = _canonical(1)
    foreign = b"foreign cleanup entry\n"
    original_rename = artifact_publisher._rename_no_replace
    original_unlink = artifact_publisher.os.unlink
    replaced_name: str | None = None
    installed_competitor = False

    def replace_inside_cleanup_move(
        source: str,
        destination: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
    ) -> None:
        nonlocal installed_competitor, replaced_name
        if destination == output.name and not installed_competitor:
            installed_competitor = True
            _write_relative_file(dst_dir_fd, destination, data)
            raise FileExistsError(destination)
        if (
            source.startswith(".artifact.json.tmp-")
            and destination.startswith(".m8-publisher-cleanup-")
            and replaced_name is None
        ):
            replaced_name = source
            original_unlink(source, dir_fd=src_dir_fd)
            _write_relative_file(src_dir_fd, source, foreign)
        original_rename(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(
        artifact_publisher,
        "_rename_no_replace",
        replace_inside_cleanup_move,
    )
    with pytest.raises(M8ArtifactPublicationError, match="identity"):
        _publish(output, _canonical(1))

    assert installed_competitor
    assert replaced_name is not None
    replacement = tmp_path / replaced_name
    assert replacement.read_bytes() == foreign
    assert output.read_bytes() == data


def test_publisher_restores_foreign_target_moved_inside_rollback_seam(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher
    from yieldforge.oracle.artifact_publisher import M8ArtifactPublicationError

    output = tmp_path / "artifact.json"
    data = _canonical(1)
    foreign = b"foreign rollback entry\n"
    original_rename = artifact_publisher._rename_no_replace
    original_fsync = artifact_publisher.os.fsync
    original_unlink = artifact_publisher.os.unlink
    parent_fsync_failed = False
    replaced = False

    def fail_install_parent_fsync(descriptor: int) -> None:
        nonlocal parent_fsync_failed
        if (
            stat.S_ISDIR(os.fstat(descriptor).st_mode)
            and output.exists()
            and not parent_fsync_failed
        ):
            parent_fsync_failed = True
            raise OSError("forced installed parent fsync failure")
        original_fsync(descriptor)

    def replace_inside_rollback_move(
        source: str,
        destination: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
    ) -> None:
        nonlocal replaced
        if (
            source == output.name
            and destination.startswith(".m8-publisher-cleanup-")
            and not replaced
        ):
            replaced = True
            original_unlink(source, dir_fd=src_dir_fd)
            _write_relative_file(src_dir_fd, source, foreign)
        original_rename(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(
        artifact_publisher,
        "_rename_no_replace",
        replace_inside_rollback_move,
    )
    monkeypatch.setattr(artifact_publisher.os, "fsync", fail_install_parent_fsync)

    with pytest.raises(M8ArtifactPublicationError, match="fsync"):
        _publish(output, data)

    assert parent_fsync_failed
    assert replaced
    assert output.read_bytes() == foreign
    assert not tuple(tmp_path.glob(".artifact.json.tmp-*"))


@pytest.mark.parametrize("failure_point", ["root", "child", "staging"])
def test_publisher_closes_new_descriptor_when_initial_fstat_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_point: str,
) -> None:
    from yieldforge.oracle import artifact_publisher
    from yieldforge.oracle.artifact_publisher import M8ArtifactPublicationError

    output = tmp_path / "artifact.json"
    original_open = artifact_publisher.os.open
    original_close = artifact_publisher.os.close
    original_fstat = artifact_publisher.os.fstat
    live_descriptors: set[int] = set()
    directory_fstat_calls = 0
    failed = False

    def recording_open(*args, **kwargs) -> int:  # type: ignore[no-untyped-def]
        descriptor = original_open(*args, **kwargs)
        live_descriptors.add(descriptor)
        return descriptor

    def recording_close(descriptor: int) -> None:
        original_close(descriptor)
        live_descriptors.discard(descriptor)

    def failing_fstat(descriptor: int) -> os.stat_result:
        nonlocal directory_fstat_calls, failed
        metadata = original_fstat(descriptor)
        if stat.S_ISDIR(metadata.st_mode):
            directory_fstat_calls += 1
            should_fail = (failure_point == "root" and directory_fstat_calls == 1) or (
                failure_point == "child" and directory_fstat_calls == 2
            )
        else:
            should_fail = failure_point == "staging" and stat.S_ISREG(metadata.st_mode)
        if should_fail and not failed:
            failed = True
            raise OSError(f"forced {failure_point} fstat failure")
        return metadata

    monkeypatch.setattr(artifact_publisher.os, "open", recording_open)
    monkeypatch.setattr(artifact_publisher.os, "close", recording_close)
    monkeypatch.setattr(artifact_publisher.os, "fstat", failing_fstat)

    with pytest.raises(M8ArtifactPublicationError) as captured:
        _publish(output, _canonical(1))

    leaked_descriptors = tuple(live_descriptors)
    for descriptor in leaked_descriptors:
        original_close(descriptor)
        live_descriptors.discard(descriptor)

    assert failed
    assert captured.value.kind in {"parent", "integrity", "identity"}
    assert leaked_descriptors == ()
    assert not output.exists()
    assert not tuple(tmp_path.glob(".artifact.json.tmp-*"))


def test_cleanup_failure_preserves_body_error_and_releases_every_descriptor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher
    from yieldforge.oracle.artifact_publisher import M8ArtifactPublicationError

    output = tmp_path / "artifact.json"
    data = _canonical(1)
    original_open = artifact_publisher.os.open
    original_close = artifact_publisher.os.close
    original_fsync = artifact_publisher.os.fsync
    original_unlink = artifact_publisher.os.unlink
    live_descriptors: set[int] = set()
    cleanup_attempted = False
    parent_fsync_calls = 0

    def recording_open(*args, **kwargs) -> int:  # type: ignore[no-untyped-def]
        descriptor = original_open(*args, **kwargs)
        live_descriptors.add(descriptor)
        return descriptor

    def recording_close(descriptor: int) -> None:
        original_close(descriptor)
        live_descriptors.discard(descriptor)

    def fail_quarantine_unlink(name: str, *, dir_fd: int) -> None:
        nonlocal cleanup_attempted
        if name.startswith(".m8-publisher-cleanup-"):
            cleanup_attempted = True
            raise OSError("forced quarantine cleanup failure")
        original_unlink(name, dir_fd=dir_fd)

    def recording_fsync(descriptor: int) -> None:
        nonlocal parent_fsync_calls
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            parent_fsync_calls += 1
            if parent_fsync_calls == 1 and output.exists():
                raise OSError("installed parent fsync sentinel")
        original_fsync(descriptor)

    monkeypatch.setattr(artifact_publisher.os, "open", recording_open)
    monkeypatch.setattr(artifact_publisher.os, "close", recording_close)
    monkeypatch.setattr(artifact_publisher.os, "fsync", recording_fsync)
    monkeypatch.setattr(artifact_publisher.os, "unlink", fail_quarantine_unlink)

    with pytest.raises(M8ArtifactPublicationError) as captured:
        _publish(output, data)

    leaked_descriptors = tuple(live_descriptors)
    for descriptor in leaked_descriptors:
        original_close(descriptor)
        live_descriptors.discard(descriptor)

    quarantines = tuple(tmp_path.glob(".m8-publisher-cleanup-*"))
    assert cleanup_attempted
    assert captured.value.kind == "fsync"
    assert isinstance(captured.value.__cause__, OSError)
    assert str(captured.value.__cause__) == "installed parent fsync sentinel"
    assert any("destination quarantine cleanup failed" in note for note in captured.value.__notes__)
    assert parent_fsync_calls >= 2
    assert leaked_descriptors == ()
    assert not output.exists()
    assert len(quarantines) == 1
    original_unlink(quarantines[0])


def test_multiple_cleanup_failures_are_reported_and_parent_is_fsynced(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher
    from yieldforge.oracle.artifact_publisher import M8ArtifactPublicationError

    output = tmp_path / "artifact.json"
    data = _canonical(1)
    original_fsync = artifact_publisher.os.fsync
    original_rename = artifact_publisher._rename_no_replace
    original_unlink = artifact_publisher.os.unlink
    parent_fsync_calls = 0

    def install_with_second_owned_name_then_raise(
        source: str,
        destination: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
    ) -> None:
        if destination != output.name:
            original_rename(
                source,
                destination,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
            )
            return
        original_rename(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        os.link(
            destination,
            source,
            src_dir_fd=dst_dir_fd,
            dst_dir_fd=src_dir_fd,
            follow_symlinks=False,
        )
        raise OSError("forced ambiguous install with two owned names")

    def fail_quarantine_unlink(name: str, *, dir_fd: int) -> None:
        if name.startswith(".m8-publisher-cleanup-"):
            raise OSError(f"forced cleanup failure for {name}")
        original_unlink(name, dir_fd=dir_fd)

    def recording_fsync(descriptor: int) -> None:
        nonlocal parent_fsync_calls
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            parent_fsync_calls += 1
        original_fsync(descriptor)

    monkeypatch.setattr(
        artifact_publisher,
        "_rename_no_replace",
        install_with_second_owned_name_then_raise,
    )
    monkeypatch.setattr(artifact_publisher.os, "fsync", recording_fsync)
    monkeypatch.setattr(artifact_publisher.os, "unlink", fail_quarantine_unlink)

    with pytest.raises(M8ArtifactPublicationError) as captured:
        _publish(output, data)

    assert captured.value.kind == "install"
    notes = captured.value.__notes__
    assert len(notes) == 2
    assert any("destination quarantine cleanup failed" in note for note in notes)
    assert any("staging quarantine cleanup failed" in note for note in notes)
    assert parent_fsync_calls == 1
    assert not output.exists()
    assert not tuple(tmp_path.glob(".artifact.json.tmp-*"))
    quarantines = tuple(tmp_path.glob(".m8-publisher-cleanup-*"))
    assert len(quarantines) == 2
    for quarantine in quarantines:
        original_unlink(quarantine)


def test_validator_cannot_deadlock_on_recursive_same_parent_publication(
    tmp_path: Path,
) -> None:
    from yieldforge.oracle.artifact_publisher import (
        M8ArtifactPublicationError,
        publish_immutable_artifact,
    )

    output = tmp_path / "artifact.json"
    nested = tmp_path / "nested.json"
    data = _canonical(1)
    validation_calls = 0
    errors: list[BaseException] = []

    def recursive_validation(value: bytes) -> bytes:
        nonlocal validation_calls
        validation_calls += 1
        if validation_calls == 1:
            _publish(nested, data)
        return _validate(value)

    def execute() -> None:
        try:
            publish_immutable_artifact(
                output,
                data,
                validate=recursive_validation,
                label="recursive test artifact",
            )
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=execute, daemon=True)
    worker.start()
    worker.join(timeout=2)

    assert not worker.is_alive(), "recursive publication deadlocked on the parent lock"
    assert len(errors) == 1
    assert isinstance(errors[0], M8ArtifactPublicationError)
    assert errors[0].kind == "validation"
    assert not output.exists()
    assert not nested.exists()


def test_validators_cannot_form_cross_parent_lock_cycle(tmp_path: Path) -> None:
    from yieldforge.oracle.artifact_publisher import (
        M8ArtifactPublicationError,
        publish_immutable_artifact,
    )

    parent_a = tmp_path / "a"
    parent_b = tmp_path / "b"
    parent_a.mkdir()
    parent_b.mkdir()
    output_a = parent_a / "artifact.json"
    output_b = parent_b / "artifact.json"
    nested_a = parent_a / "nested.json"
    nested_b = parent_b / "nested.json"
    data = _canonical(1)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def recursive_validation(nested: Path):
        validation_calls = 0

        def validate(value: bytes) -> bytes:
            nonlocal validation_calls
            validation_calls += 1
            if validation_calls == 1:
                barrier.wait(timeout=2)
                _publish(nested, data)
            return _validate(value)

        return validate

    def execute(output: Path, nested: Path) -> None:
        try:
            publish_immutable_artifact(
                output,
                data,
                validate=recursive_validation(nested),
                label=f"recursive test artifact {output.parent.name}",
            )
        except BaseException as error:
            errors.append(error)

    workers = (
        threading.Thread(target=execute, args=(output_a, nested_b), daemon=True),
        threading.Thread(target=execute, args=(output_b, nested_a), daemon=True),
    )
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=3)

    assert not any(worker.is_alive() for worker in workers), "publisher lock cycle deadlocked"
    assert len(errors) == 2
    assert all(isinstance(error, M8ArtifactPublicationError) for error in errors)
    assert all(error.kind == "validation" for error in errors)
    assert not output_a.exists()
    assert not output_b.exists()
    assert not nested_a.exists()
    assert not nested_b.exists()


def test_validator_child_thread_cannot_deadlock_on_parent_lock(tmp_path: Path) -> None:
    from yieldforge.oracle.artifact_publisher import publish_immutable_artifact

    output = tmp_path / "artifact.json"
    nested = tmp_path / "nested.json"
    data = _canonical(1)
    validation_calls = 0
    outer_errors: list[BaseException] = []
    child_errors: list[BaseException] = []

    def execute_nested() -> None:
        try:
            _publish(nested, data)
        except BaseException as error:
            child_errors.append(error)

    def threaded_validation(value: bytes) -> bytes:
        nonlocal validation_calls
        validation_calls += 1
        if validation_calls == 1:
            child = threading.Thread(target=execute_nested, daemon=True)
            child.start()
            child.join()
            if child_errors:
                raise child_errors[0]
        return _validate(value)

    def execute_outer() -> None:
        try:
            publish_immutable_artifact(
                output,
                data,
                validate=threaded_validation,
                label="threaded recursive test artifact",
            )
        except BaseException as error:
            outer_errors.append(error)

    outer = threading.Thread(target=execute_outer, daemon=True)
    outer.start()
    outer.join(timeout=2)

    assert not outer.is_alive(), "validator child thread deadlocked on the parent lock"
    assert child_errors == []
    assert outer_errors == []
    assert output.read_bytes() == data
    assert nested.read_bytes() == data


def test_existing_publish_rejects_same_inode_rewrite_after_file_fsync(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher
    from yieldforge.oracle.artifact_publisher import M8ArtifactPublicationError

    output = tmp_path / "artifact.json"
    data = _canonical(1)
    output.write_bytes(data)
    original_identity = output.stat().st_dev, output.stat().st_ino
    original_fsync = artifact_publisher.os.fsync
    file_fsynced = False
    rewritten = False

    def rewrite_after_file_fsync(descriptor: int) -> None:
        nonlocal file_fsynced, rewritten
        metadata = os.fstat(descriptor)
        if stat.S_ISREG(metadata.st_mode):
            file_fsynced = True
        elif file_fsynced and not rewritten:
            rewritten = True
            before = output.stat()
            output.write_bytes(_canonical(2))
            output.write_bytes(data)
            os.utime(
                output,
                ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000),
            )
        original_fsync(descriptor)

    monkeypatch.setattr(artifact_publisher.os, "fsync", rewrite_after_file_fsync)

    with pytest.raises(M8ArtifactPublicationError, match="identity"):
        _publish(output, data)

    assert rewritten
    assert (output.stat().st_dev, output.stat().st_ino) == original_identity
    assert output.read_bytes() == data


def test_installed_publish_rejects_same_inode_rewrite_after_staging_fsync(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher
    from yieldforge.oracle.artifact_publisher import M8ArtifactPublicationError

    output = tmp_path / "artifact.json"
    data = _canonical(1)
    original_fsync = artifact_publisher.os.fsync
    rewritten = False

    def rewrite_during_install_parent_fsync(descriptor: int) -> None:
        nonlocal rewritten
        if stat.S_ISDIR(os.fstat(descriptor).st_mode) and output.exists() and not rewritten:
            rewritten = True
            before = output.stat()
            output.write_bytes(_canonical(2))
            output.write_bytes(data)
            os.utime(
                output,
                ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000),
            )
        original_fsync(descriptor)

    monkeypatch.setattr(
        artifact_publisher.os,
        "fsync",
        rewrite_during_install_parent_fsync,
    )

    with pytest.raises(M8ArtifactPublicationError) as captured:
        _publish(output, data)

    assert captured.value.kind == "identity"
    assert rewritten
    assert not output.exists()
    assert not tuple(tmp_path.glob(".artifact.json.tmp-*"))


def test_validator_finishes_before_contending_publisher_waits_on_parent_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher
    from yieldforge.oracle.artifact_publisher import publish_immutable_artifact

    output_a = tmp_path / "a.json"
    output_b = tmp_path / "b.json"
    data = _canonical(1)
    original_fsync = artifact_publisher.os.fsync
    original_lock = artifact_publisher._lock_parent_directory
    b_at_lock = threading.Event()
    b_started = False
    validation_calls = 0
    errors: list[BaseException] = []
    results: list[Path] = []

    def lock_with_signal(descriptor: int, *, label: str) -> None:
        if threading.current_thread().name == "publisher-b":
            b_at_lock.set()
        original_lock(descriptor, label=label)

    def run_b() -> None:
        try:
            results.append(_publish(output_b, data))
        except BaseException as error:
            errors.append(error)

    worker_b = threading.Thread(target=run_b, name="publisher-b", daemon=True)

    def start_b_during_staging_fsync(descriptor: int) -> None:
        nonlocal b_started
        if (
            threading.current_thread().name == "publisher-a"
            and stat.S_ISREG(os.fstat(descriptor).st_mode)
            and not b_started
        ):
            b_started = True
            worker_b.start()
            assert b_at_lock.wait(timeout=2)
        original_fsync(descriptor)

    def validate_a(value: bytes) -> bytes:
        nonlocal validation_calls
        validation_calls += 1
        assert artifact_publisher._active_parent_identities() == set()
        return _validate(value)

    def run_a() -> None:
        try:
            results.append(
                publish_immutable_artifact(
                    output_a,
                    data,
                    validate=validate_a,
                    label="publisher a artifact",
                )
            )
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(artifact_publisher, "_lock_parent_directory", lock_with_signal)
    monkeypatch.setattr(artifact_publisher.os, "fsync", start_b_during_staging_fsync)
    worker_a = threading.Thread(target=run_a, name="publisher-a", daemon=True)
    worker_a.start()
    worker_a.join(timeout=3)
    worker_b.join(timeout=3)

    assert not worker_a.is_alive()
    assert not worker_b.is_alive()
    assert validation_calls == 1
    assert errors == []
    assert set(results) == {output_a, output_b}
    assert output_a.read_bytes() == data
    assert output_b.read_bytes() == data


def test_slow_validator_does_not_reject_unrelated_concurrent_publication(
    tmp_path: Path,
) -> None:
    from yieldforge.oracle.artifact_publisher import publish_immutable_artifact

    parent_a = tmp_path / "a"
    parent_b = tmp_path / "b"
    parent_a.mkdir()
    parent_b.mkdir()
    output_a = parent_a / "artifact.json"
    output_b = parent_b / "artifact.json"
    data = _canonical(1)
    validation_entered = threading.Event()
    release_validation = threading.Event()
    errors: list[BaseException] = []

    def slow_validate(value: bytes) -> bytes:
        validation_entered.set()
        assert release_validation.wait(timeout=3)
        return _validate(value)

    def run_a() -> None:
        try:
            publish_immutable_artifact(
                output_a,
                data,
                validate=slow_validate,
                label="slow artifact",
            )
        except BaseException as error:
            errors.append(error)

    worker_a = threading.Thread(target=run_a, daemon=True)
    worker_a.start()
    assert validation_entered.wait(timeout=2)
    try:
        assert _publish(output_b, data) == output_b
    finally:
        release_validation.set()
        worker_a.join(timeout=3)

    assert not worker_a.is_alive()
    assert errors == []
    assert output_a.read_bytes() == data
    assert output_b.read_bytes() == data


def test_early_staging_metadata_failure_cleanup_is_durable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher
    from yieldforge.oracle.artifact_publisher import M8ArtifactPublicationError

    output = tmp_path / "artifact.json"
    original_fstat = artifact_publisher.os.fstat
    original_fsync = artifact_publisher.os.fsync
    failed = False
    parent_fsyncs = 0

    def fail_first_staging_fstat(descriptor: int):  # type: ignore[no-untyped-def]
        nonlocal failed
        metadata = original_fstat(descriptor)
        if stat.S_ISREG(metadata.st_mode) and not failed:
            failed = True
            raise OSError("staging metadata sentinel")
        return metadata

    def record_parent_fsync(descriptor: int) -> None:
        nonlocal parent_fsyncs
        if stat.S_ISDIR(original_fstat(descriptor).st_mode):
            parent_fsyncs += 1
        original_fsync(descriptor)

    monkeypatch.setattr(artifact_publisher.os, "fstat", fail_first_staging_fstat)
    monkeypatch.setattr(artifact_publisher.os, "fsync", record_parent_fsync)

    with pytest.raises(M8ArtifactPublicationError, match="metadata"):
        _publish(output, _canonical(1))

    assert failed
    assert parent_fsyncs == 1
    assert not output.exists()
    assert not tuple(tmp_path.iterdir())


def test_early_staging_cleanup_failure_preserves_metadata_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher
    from yieldforge.oracle.artifact_publisher import M8ArtifactPublicationError

    output = tmp_path / "artifact.json"
    original_fstat = artifact_publisher.os.fstat
    original_fsync = artifact_publisher.os.fsync
    original_unlink = artifact_publisher.os.unlink
    failed = False
    parent_fsyncs = 0

    def fail_first_staging_fstat(descriptor: int):  # type: ignore[no-untyped-def]
        nonlocal failed
        metadata = original_fstat(descriptor)
        if stat.S_ISREG(metadata.st_mode) and not failed:
            failed = True
            raise OSError("staging metadata sentinel")
        return metadata

    def fail_quarantine_unlink(name: str, *, dir_fd: int) -> None:
        if name.startswith(".m8-publisher-cleanup-"):
            raise OSError("cleanup sentinel")
        original_unlink(name, dir_fd=dir_fd)

    def record_parent_fsync(descriptor: int) -> None:
        nonlocal parent_fsyncs
        if stat.S_ISDIR(original_fstat(descriptor).st_mode):
            parent_fsyncs += 1
        original_fsync(descriptor)

    monkeypatch.setattr(artifact_publisher.os, "fstat", fail_first_staging_fstat)
    monkeypatch.setattr(artifact_publisher.os, "fsync", record_parent_fsync)
    monkeypatch.setattr(artifact_publisher.os, "unlink", fail_quarantine_unlink)

    with pytest.raises(M8ArtifactPublicationError, match="metadata") as captured:
        _publish(output, _canonical(1))

    assert "metadata" in captured.value.detail
    assert any("quarantine cleanup failed" in note for note in captured.value.__notes__)
    assert parent_fsyncs == 1
    quarantines = tuple(tmp_path.glob(".m8-publisher-cleanup-*"))
    assert len(quarantines) == 1
    original_unlink(quarantines[0])


def test_incomplete_publish_cleans_owned_stage_migrated_to_destination(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher
    from yieldforge.oracle.artifact_publisher import M8ArtifactPublicationError

    output = tmp_path / "artifact.json"
    data = _canonical(1)
    original_rename = artifact_publisher._rename_no_replace
    original_fsync = artifact_publisher.os.fsync
    migrated = False

    def install_competitor(
        source: str,
        destination: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
    ) -> None:
        if destination != output.name:
            original_rename(
                source,
                destination,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
            )
            return
        _write_relative_file(dst_dir_fd, destination, data)
        raise FileExistsError(destination)

    def migrate_stage_during_competitor_fsync(descriptor: int) -> None:
        nonlocal migrated
        metadata = os.fstat(descriptor)
        original_fsync(descriptor)
        stages = tuple(tmp_path.glob(".artifact.json.tmp-*"))
        if (
            stat.S_ISREG(metadata.st_mode)
            and output.exists()
            and stages
            and (metadata.st_dev, metadata.st_ino)
            == (output.stat().st_dev, output.stat().st_ino)
            and not migrated
        ):
            migrated = True
            stage = next(tmp_path.glob(".artifact.json.tmp-*"))
            output.unlink()
            stage.rename(output)

    monkeypatch.setattr(artifact_publisher, "_rename_no_replace", install_competitor)
    monkeypatch.setattr(
        artifact_publisher.os,
        "fsync",
        migrate_stage_during_competitor_fsync,
    )

    with pytest.raises(M8ArtifactPublicationError) as captured:
        _publish(output, data)

    assert captured.value.kind == "identity"
    assert migrated
    assert not output.exists()
    assert not tuple(tmp_path.glob(".artifact.json.tmp-*"))


def test_incomplete_publish_cleans_owned_final_migrated_back_to_original_stage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher
    from yieldforge.oracle.artifact_publisher import M8ArtifactPublicationError

    output = tmp_path / "artifact.json"
    data = _canonical(1)
    original_rename = artifact_publisher._rename_no_replace
    original_fsync = artifact_publisher.os.fsync
    original_stage_name: str | None = None
    migrated = False

    def capture_install(
        source: str,
        destination: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
    ) -> None:
        nonlocal original_stage_name
        if destination == output.name:
            original_stage_name = source
        original_rename(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    def migrate_during_parent_fsync(descriptor: int) -> None:
        nonlocal migrated
        if (
            stat.S_ISDIR(os.fstat(descriptor).st_mode)
            and output.exists()
            and original_stage_name is not None
            and not migrated
        ):
            migrated = True
            output.rename(tmp_path / original_stage_name)
        original_fsync(descriptor)

    monkeypatch.setattr(artifact_publisher, "_rename_no_replace", capture_install)
    monkeypatch.setattr(artifact_publisher.os, "fsync", migrate_during_parent_fsync)

    with pytest.raises(M8ArtifactPublicationError) as captured:
        _publish(output, data)

    assert captured.value.kind == "identity"
    assert migrated
    assert not output.exists()
    assert not tuple(tmp_path.glob(".artifact.json.tmp-*"))


def test_post_install_base_exception_rolls_back_and_fsyncs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher

    output = tmp_path / "artifact.json"
    data = _canonical(1)
    original_rename = artifact_publisher._rename_no_replace
    original_fsync = artifact_publisher.os.fsync
    parent_fsyncs = 0

    def install_then_interrupt(
        source: str,
        destination: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
    ) -> None:
        original_rename(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        raise KeyboardInterrupt("post-install interrupt")

    def record_parent_fsync(descriptor: int) -> None:
        nonlocal parent_fsyncs
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            parent_fsyncs += 1
        original_fsync(descriptor)

    monkeypatch.setattr(artifact_publisher, "_rename_no_replace", install_then_interrupt)
    monkeypatch.setattr(artifact_publisher.os, "fsync", record_parent_fsync)

    with pytest.raises(KeyboardInterrupt, match="post-install interrupt"):
        _publish(output, data)

    assert parent_fsyncs == 1
    assert not output.exists()
    assert not tuple(tmp_path.glob(".artifact.json.tmp-*"))


def test_cleanup_interrupt_preserves_body_error_and_releases_every_descriptor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher
    from yieldforge.oracle.artifact_publisher import M8ArtifactPublicationError

    output = tmp_path / "artifact.json"
    data = _canonical(1)
    original_open = artifact_publisher.os.open
    original_close = artifact_publisher.os.close
    original_fsync = artifact_publisher.os.fsync
    live_descriptors: set[int] = set()
    parent_fsyncs = 0

    def record_open(*args, **kwargs) -> int:  # type: ignore[no-untyped-def]
        descriptor = original_open(*args, **kwargs)
        live_descriptors.add(descriptor)
        return descriptor

    def record_close(descriptor: int) -> None:
        original_close(descriptor)
        live_descriptors.discard(descriptor)

    def fail_body_then_interrupt_cleanup(descriptor: int) -> None:
        nonlocal parent_fsyncs
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            parent_fsyncs += 1
            if parent_fsyncs == 1:
                raise OSError("publication parent fsync sentinel")
            if parent_fsyncs == 2:
                raise KeyboardInterrupt("rollback fsync interrupt")
        original_fsync(descriptor)

    monkeypatch.setattr(artifact_publisher.os, "open", record_open)
    monkeypatch.setattr(artifact_publisher.os, "close", record_close)
    monkeypatch.setattr(artifact_publisher.os, "fsync", fail_body_then_interrupt_cleanup)

    with pytest.raises(M8ArtifactPublicationError, match="fsync") as captured:
        _publish(output, data)

    assert any("rollback fsync interrupt" in note for note in captured.value.__notes__)
    assert live_descriptors == set()
    assert artifact_publisher._active_parent_identities() == set()
    assert not output.exists()
    assert not tuple(tmp_path.glob(".artifact.json.tmp-*"))


def test_staging_rewrite_after_file_fsync_cannot_be_published(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher
    from yieldforge.oracle.artifact_publisher import M8ArtifactPublicationError

    output = tmp_path / "artifact.json"
    data = _canonical(1)
    original_fsync = artifact_publisher.os.fsync
    mutated = False

    def mutate_after_real_file_fsync(descriptor: int) -> None:
        nonlocal mutated
        metadata = os.fstat(descriptor)
        original_fsync(descriptor)
        if stat.S_ISREG(metadata.st_mode) and not mutated:
            mutated = True
            os.lseek(descriptor, 0, os.SEEK_SET)
            os.ftruncate(descriptor, 0)
            os.write(descriptor, _canonical(2))
            os.lseek(descriptor, 0, os.SEEK_SET)
            os.ftruncate(descriptor, 0)
            os.write(descriptor, data)

    monkeypatch.setattr(artifact_publisher.os, "fsync", mutate_after_real_file_fsync)

    with pytest.raises(M8ArtifactPublicationError) as captured:
        _publish(output, data)

    assert captured.value.kind == "identity"
    assert mutated
    assert not output.exists()
    assert not tuple(tmp_path.glob(".artifact.json.tmp-*"))


def test_cleanup_move_then_raise_reconciles_owned_quarantine(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher
    from yieldforge.oracle.artifact_publisher import M8ArtifactPublicationError

    output = tmp_path / "artifact.json"
    data = _canonical(1)
    original_rename = artifact_publisher._rename_no_replace
    original_fsync = artifact_publisher.os.fsync
    parent_fsync_failed = False
    ambiguous_cleanup = False

    def move_cleanup_then_raise(
        source: str,
        destination: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
    ) -> None:
        nonlocal ambiguous_cleanup
        original_rename(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        if destination.startswith(".m8-publisher-cleanup-"):
            ambiguous_cleanup = True
            raise OSError("cleanup move reported failure")

    def fail_install_parent_fsync(descriptor: int) -> None:
        nonlocal parent_fsync_failed
        if (
            stat.S_ISDIR(os.fstat(descriptor).st_mode)
            and output.exists()
            and not parent_fsync_failed
        ):
            parent_fsync_failed = True
            raise OSError("installed parent fsync sentinel")
        original_fsync(descriptor)

    monkeypatch.setattr(artifact_publisher, "_rename_no_replace", move_cleanup_then_raise)
    monkeypatch.setattr(artifact_publisher.os, "fsync", fail_install_parent_fsync)

    with pytest.raises(M8ArtifactPublicationError, match="fsync") as captured:
        _publish(output, data)

    assert parent_fsync_failed
    assert ambiguous_cleanup
    assert any("quarantine move" in note for note in captured.value.__notes__)
    assert not output.exists()
    assert not tuple(tmp_path.glob(".artifact.json.tmp-*"))
    assert not tuple(tmp_path.glob(".m8-publisher-cleanup-*"))


def test_staging_fstat_base_exception_recovers_identity_for_durable_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher

    output = tmp_path / "artifact.json"
    original_fstat = artifact_publisher.os.fstat
    original_fsync = artifact_publisher.os.fsync
    interrupted = False
    parent_fsyncs = 0

    def interrupt_first_staging_fstat(descriptor: int):  # type: ignore[no-untyped-def]
        nonlocal interrupted
        metadata = original_fstat(descriptor)
        if stat.S_ISREG(metadata.st_mode) and not interrupted:
            interrupted = True
            raise KeyboardInterrupt("staging fstat interrupt")
        return metadata

    def record_parent_fsync(descriptor: int) -> None:
        nonlocal parent_fsyncs
        if stat.S_ISDIR(original_fstat(descriptor).st_mode):
            parent_fsyncs += 1
        original_fsync(descriptor)

    monkeypatch.setattr(artifact_publisher.os, "fstat", interrupt_first_staging_fstat)
    monkeypatch.setattr(artifact_publisher.os, "fsync", record_parent_fsync)

    with pytest.raises(KeyboardInterrupt, match="staging fstat interrupt"):
        _publish(output, _canonical(1))

    assert interrupted
    assert parent_fsyncs == 1
    assert not output.exists()
    assert not tuple(tmp_path.glob(".artifact.json.tmp-*"))


def test_staging_close_interrupt_is_not_retried_after_ambiguous_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher

    output = tmp_path / "artifact.json"
    original_open = artifact_publisher.os.open
    original_close = artifact_publisher.os.close
    original_fstat = artifact_publisher.os.fstat
    live_descriptors: set[int] = set()
    interrupted = False

    def record_open(*args, **kwargs) -> int:  # type: ignore[no-untyped-def]
        descriptor = original_open(*args, **kwargs)
        live_descriptors.add(descriptor)
        return descriptor

    def interrupt_first_staging_close(descriptor: int) -> None:
        nonlocal interrupted
        if stat.S_ISREG(original_fstat(descriptor).st_mode) and not interrupted:
            interrupted = True
            raise KeyboardInterrupt("staging close interrupt")
        original_close(descriptor)
        live_descriptors.discard(descriptor)

    monkeypatch.setattr(artifact_publisher.os, "open", record_open)
    monkeypatch.setattr(artifact_publisher.os, "close", interrupt_first_staging_close)

    with pytest.raises(KeyboardInterrupt, match="staging close interrupt"):
        _publish(output, _canonical(1))

    leaked_descriptors = tuple(live_descriptors)
    for descriptor in leaked_descriptors:
        original_close(descriptor)
        live_descriptors.discard(descriptor)

    assert interrupted
    assert len(leaked_descriptors) == 1
    assert not output.exists()
    assert not tuple(tmp_path.glob(".artifact.json.tmp-*"))


def test_cleanup_move_interrupt_before_side_effect_retries_owned_removal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher
    from yieldforge.oracle.artifact_publisher import M8ArtifactPublicationError

    output = tmp_path / "artifact.json"
    data = _canonical(1)
    original_rename = artifact_publisher._rename_no_replace
    original_fsync = artifact_publisher.os.fsync
    parent_fsync_failed = False
    cleanup_interrupted = False

    def interrupt_cleanup_before_move(
        source: str,
        destination: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
    ) -> None:
        nonlocal cleanup_interrupted
        if destination.startswith(".m8-publisher-cleanup-") and not cleanup_interrupted:
            cleanup_interrupted = True
            raise KeyboardInterrupt("cleanup move interrupt")
        original_rename(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    def fail_install_parent_fsync(descriptor: int) -> None:
        nonlocal parent_fsync_failed
        if (
            stat.S_ISDIR(os.fstat(descriptor).st_mode)
            and output.exists()
            and not parent_fsync_failed
        ):
            parent_fsync_failed = True
            raise OSError("installed parent fsync sentinel")
        original_fsync(descriptor)

    monkeypatch.setattr(
        artifact_publisher,
        "_rename_no_replace",
        interrupt_cleanup_before_move,
    )
    monkeypatch.setattr(artifact_publisher.os, "fsync", fail_install_parent_fsync)

    with pytest.raises(M8ArtifactPublicationError, match="fsync") as captured:
        _publish(output, data)

    assert parent_fsync_failed
    assert cleanup_interrupted
    assert any("cleanup move interrupt" in note for note in captured.value.__notes__)
    assert not output.exists()
    assert not tuple(tmp_path.glob(".artifact.json.tmp-*"))
    assert not tuple(tmp_path.glob(".m8-publisher-cleanup-*"))


def test_directory_close_interrupt_is_not_retried_after_ambiguous_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher

    output = tmp_path / "artifact.json"
    data = _canonical(1)
    original_open = artifact_publisher.os.open
    original_close = artifact_publisher.os.close
    original_fstat = artifact_publisher.os.fstat
    live_descriptors: set[int] = set()
    interrupted = False

    def record_open(*args, **kwargs) -> int:  # type: ignore[no-untyped-def]
        descriptor = original_open(*args, **kwargs)
        live_descriptors.add(descriptor)
        return descriptor

    def interrupt_first_directory_close(descriptor: int) -> None:
        nonlocal interrupted
        if stat.S_ISDIR(original_fstat(descriptor).st_mode) and not interrupted:
            interrupted = True
            raise KeyboardInterrupt("directory close interrupt")
        original_close(descriptor)
        live_descriptors.discard(descriptor)

    monkeypatch.setattr(artifact_publisher.os, "open", record_open)
    monkeypatch.setattr(artifact_publisher.os, "close", interrupt_first_directory_close)

    with pytest.raises(KeyboardInterrupt, match="directory close interrupt"):
        _publish(output, data)

    leaked_descriptors = tuple(live_descriptors)
    for descriptor in leaked_descriptors:
        original_close(descriptor)
        live_descriptors.discard(descriptor)

    assert interrupted
    assert len(leaked_descriptors) == 1
    assert output.read_bytes() == data


def test_stable_read_close_interrupt_is_not_retried_after_ambiguous_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher

    output = tmp_path / "artifact.json"
    original_open = artifact_publisher.os.open
    original_close = artifact_publisher.os.close
    original_fstat = artifact_publisher.os.fstat
    live_descriptors: set[int] = set()
    interrupted = False

    def record_open(*args, **kwargs) -> int:  # type: ignore[no-untyped-def]
        descriptor = original_open(*args, **kwargs)
        live_descriptors.add(descriptor)
        return descriptor

    def interrupt_first_read_close(descriptor: int) -> None:
        nonlocal interrupted
        metadata = original_fstat(descriptor)
        flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        if (
            stat.S_ISREG(metadata.st_mode)
            and flags & os.O_ACCMODE == os.O_RDONLY
            and not interrupted
        ):
            interrupted = True
            raise KeyboardInterrupt("stable read close interrupt")
        original_close(descriptor)
        live_descriptors.discard(descriptor)

    monkeypatch.setattr(artifact_publisher.os, "open", record_open)
    monkeypatch.setattr(artifact_publisher.os, "close", interrupt_first_read_close)

    with pytest.raises(KeyboardInterrupt, match="stable read close interrupt"):
        _publish(output, _canonical(1))

    leaked_descriptors = tuple(live_descriptors)
    for descriptor in leaked_descriptors:
        original_close(descriptor)
        live_descriptors.discard(descriptor)

    assert interrupted
    assert len(leaked_descriptors) == 1
    assert not output.exists()
    assert not tuple(tmp_path.glob(".artifact.json.tmp-*"))


def test_exact_file_fsync_close_interrupt_is_not_retried_after_ambiguous_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher

    output = tmp_path / "artifact.json"
    data = _canonical(1)
    output.write_bytes(data)
    original_open = artifact_publisher.os.open
    original_close = artifact_publisher.os.close
    original_fstat = artifact_publisher.os.fstat
    live_descriptors: set[int] = set()
    regular_read_closes = 0
    interrupted = False

    def record_open(*args, **kwargs) -> int:  # type: ignore[no-untyped-def]
        descriptor = original_open(*args, **kwargs)
        live_descriptors.add(descriptor)
        return descriptor

    def interrupt_second_read_close(descriptor: int) -> None:
        nonlocal regular_read_closes, interrupted
        metadata = original_fstat(descriptor)
        flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        if stat.S_ISREG(metadata.st_mode) and flags & os.O_ACCMODE == os.O_RDONLY:
            regular_read_closes += 1
            if regular_read_closes == 2 and not interrupted:
                interrupted = True
                raise KeyboardInterrupt("exact fsync close interrupt")
        original_close(descriptor)
        live_descriptors.discard(descriptor)

    monkeypatch.setattr(artifact_publisher.os, "open", record_open)
    monkeypatch.setattr(artifact_publisher.os, "close", interrupt_second_read_close)

    with pytest.raises(KeyboardInterrupt, match="exact fsync close interrupt"):
        _publish(output, data)

    leaked_descriptors = tuple(live_descriptors)
    for descriptor in leaked_descriptors:
        original_close(descriptor)
        live_descriptors.discard(descriptor)

    assert interrupted
    assert regular_read_closes >= 2
    assert len(leaked_descriptors) == 1
    assert output.read_bytes() == data


def test_new_directory_fd_close_interrupt_during_open_failure_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher
    from yieldforge.oracle.artifact_publisher import M8ArtifactPublicationError

    output = tmp_path / "artifact.json"
    original_open = artifact_publisher.os.open
    original_close = artifact_publisher.os.close
    original_fstat = artifact_publisher.os.fstat
    live_descriptors: set[int] = set()
    fstat_failed = False
    close_interrupted = False

    def record_open(*args, **kwargs) -> int:  # type: ignore[no-untyped-def]
        descriptor = original_open(*args, **kwargs)
        live_descriptors.add(descriptor)
        return descriptor

    def fail_first_directory_fstat(descriptor: int):  # type: ignore[no-untyped-def]
        nonlocal fstat_failed
        metadata = original_fstat(descriptor)
        if stat.S_ISDIR(metadata.st_mode) and not fstat_failed:
            fstat_failed = True
            raise OSError("directory fstat sentinel")
        return metadata

    def interrupt_first_directory_close(descriptor: int) -> None:
        nonlocal close_interrupted
        if stat.S_ISDIR(original_fstat(descriptor).st_mode) and not close_interrupted:
            close_interrupted = True
            raise KeyboardInterrupt("directory open-failure close interrupt")
        original_close(descriptor)
        live_descriptors.discard(descriptor)

    monkeypatch.setattr(artifact_publisher.os, "open", record_open)
    monkeypatch.setattr(artifact_publisher.os, "fstat", fail_first_directory_fstat)
    monkeypatch.setattr(artifact_publisher.os, "close", interrupt_first_directory_close)

    with pytest.raises(M8ArtifactPublicationError, match="parent"):
        _publish(output, _canonical(1))

    leaked_descriptors = tuple(live_descriptors)
    for descriptor in leaked_descriptors:
        original_close(descriptor)
        live_descriptors.discard(descriptor)

    assert fstat_failed
    assert close_interrupted
    assert len(leaked_descriptors) == 1
    assert not output.exists()


@pytest.mark.parametrize("close_kind", ["staging", "read", "directory"])
def test_close_error_with_fd_reuse_never_closes_foreign_descriptor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    close_kind: str,
) -> None:
    from yieldforge.oracle import artifact_publisher

    output = tmp_path / "artifact.json"
    data = _canonical(1)
    sentinel = tmp_path / "foreign-sentinel.txt"
    sentinel.write_bytes(b"foreign descriptor remains owned\n")
    if close_kind == "read":
        output.write_bytes(data)

    original_open = artifact_publisher.os.open
    original_close = artifact_publisher.os.close
    original_fstat = artifact_publisher.os.fstat
    reused_descriptor: int | None = None

    def close_then_reuse_and_raise(descriptor: int) -> None:
        nonlocal reused_descriptor
        metadata = original_fstat(descriptor)
        flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        is_regular = stat.S_ISREG(metadata.st_mode)
        matches = (
            (close_kind == "staging" and is_regular and flags & os.O_ACCMODE == os.O_RDWR)
            or (close_kind == "read" and is_regular and flags & os.O_ACCMODE == os.O_RDONLY)
            or (close_kind == "directory" and stat.S_ISDIR(metadata.st_mode))
        )
        if matches and reused_descriptor is None:
            original_close(descriptor)
            reused_descriptor = original_open(sentinel, os.O_RDONLY | os.O_CLOEXEC)
            assert reused_descriptor == descriptor
            raise KeyboardInterrupt(f"{close_kind} close completed before error")
        original_close(descriptor)

    monkeypatch.setattr(artifact_publisher.os, "close", close_then_reuse_and_raise)

    with pytest.raises(KeyboardInterrupt, match="close completed before error"):
        _publish(output, data)

    assert reused_descriptor is not None
    try:
        assert stat.S_ISREG(original_fstat(reused_descriptor).st_mode)
        assert os.pread(reused_descriptor, 64, 0) == sentinel.read_bytes()
    finally:
        original_close(reused_descriptor)


def test_close_error_with_same_inode_fd_reuse_never_closes_foreign_descriptor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher

    output = tmp_path / "artifact.json"
    data = _canonical(1)
    output.write_bytes(data)
    original_close = artifact_publisher.os.close
    original_fstat = artifact_publisher.os.fstat
    reused_descriptor: int | None = None

    def close_then_reopen_same_inode_and_raise(descriptor: int) -> None:
        nonlocal reused_descriptor
        metadata = original_fstat(descriptor)
        flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        if (
            stat.S_ISREG(metadata.st_mode)
            and flags & os.O_ACCMODE == os.O_RDONLY
            and reused_descriptor is None
        ):
            offset = os.lseek(descriptor, 0, os.SEEK_CUR)
            original_close(descriptor)
            reused_descriptor = os.open(output, artifact_publisher._FILE_READ_FLAGS)
            os.lseek(reused_descriptor, offset, os.SEEK_SET)
            assert reused_descriptor == descriptor
            raise KeyboardInterrupt("same-inode close completed before error")
        original_close(descriptor)

    monkeypatch.setattr(
        artifact_publisher.os,
        "close",
        close_then_reopen_same_inode_and_raise,
    )

    with pytest.raises(KeyboardInterrupt, match="same-inode close completed before error"):
        _publish(output, data)

    assert reused_descriptor is not None
    try:
        assert original_fstat(reused_descriptor).st_ino == output.stat().st_ino
        assert os.pread(reused_descriptor, len(data), 0) == data
    finally:
        original_close(reused_descriptor)


def test_interrupt_after_parent_acquire_does_not_poison_later_publication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import artifact_publisher

    output = tmp_path / "artifact.json"
    data = _canonical(1)
    original_enter = artifact_publisher._enter_parent_publication
    interrupted = False

    def enter_then_interrupt(*args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        nonlocal interrupted
        original_enter(*args, **kwargs)
        interrupted = True
        raise KeyboardInterrupt("post-acquire interrupt")

    monkeypatch.setattr(
        artifact_publisher,
        "_enter_parent_publication",
        enter_then_interrupt,
    )
    with pytest.raises(KeyboardInterrupt, match="post-acquire interrupt"):
        _publish(output, data)

    monkeypatch.setattr(
        artifact_publisher,
        "_enter_parent_publication",
        original_enter,
    )
    _publish(output, data)

    assert interrupted
    assert output.read_bytes() == data
    assert artifact_publisher._active_parent_identities() == set()
