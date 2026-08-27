from __future__ import annotations

import os
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

import pytest


def _pickleable_alternate_operation() -> str:
    return "alternate implementation"


def _write_source_tree(root: Path) -> tuple[Path, Path]:
    package = root / "yieldforge"
    package.mkdir(parents=True)
    primary = package / "worker.py"
    primary.write_text("VALUE = 1\n")
    (package / "helper.py").write_text("HELPER = 2\n")
    return package, primary


def _load_attested_test_operations(
    package: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> ModuleType:
    source = package / "operation.py"
    source.write_text(
        "from pathlib import Path\n"
        "\n"
        "def return_value():\n"
        "    return 'reachable'\n"
        "\n"
        "def mutate_then_return(path):\n"
        "    Path(path).write_text('VALUE = 2\\n')\n"
        "    return 'must not escape'\n"
        "\n"
        "def mutate_then_fail(path):\n"
        "    Path(path).write_text('VALUE = 2\\n')\n"
        "    raise RuntimeError('operation failed')\n"
    )
    spec = spec_from_file_location("yieldforge.operation", source)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_source_attested_operation_rejects_changed_worker_tree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle.source_attestation import (
        SourceAttestedOperation,
        capture_source_tree,
    )

    package, primary = _write_source_tree(tmp_path)
    module = _load_attested_test_operations(package, monkeypatch)
    snapshot = capture_source_tree(package)
    operation = SourceAttestedOperation(
        operation=module.return_value,
        source_tree=snapshot,
        expected_module_name=module.__name__,
        expected_function_name="return_value",
    )
    primary.write_text("VALUE = 2\n")

    with pytest.raises(ValueError, match="source tree changed"):
        operation()


def test_source_attested_operation_rejects_pickleable_external_operation() -> None:
    from yieldforge.oracle.source_attestation import (
        SourceAttestedOperation,
        capture_source_tree,
    )

    with pytest.raises(ValueError, match="loaded operation"):
        SourceAttestedOperation(
            operation=_pickleable_alternate_operation,
            source_tree=capture_source_tree(),
            expected_module_name="yieldforge.oracle.experiment",
            expected_function_name="_profile_portable_generation_worker",
        )


def test_source_attested_operation_rejects_a_different_attested_operation() -> None:
    from yieldforge.oracle import experiment
    from yieldforge.oracle.source_attestation import (
        SourceAttestedOperation,
        capture_source_tree,
    )

    with pytest.raises(ValueError, match="loaded operation"):
        SourceAttestedOperation(
            operation=capture_source_tree,
            source_tree=capture_source_tree(),
            expected_module_name=experiment.__name__,
            expected_function_name="_profile_portable_generation_worker",
        )


def test_source_attested_operation_rejects_in_memory_loaded_code_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import experiment
    from yieldforge.oracle.source_attestation import (
        SourceAttestedOperation,
        capture_source_tree,
    )

    operation_name = "_profile_portable_generation_worker"
    namespace = {"__name__": experiment.__name__}
    exec(
        compile(
            f"def {operation_name}(*args, **kwargs):\n    return 'forged'\n",
            str(Path(experiment.__file__).resolve()),
            "exec",
        ),
        namespace,
    )
    forged = namespace[operation_name]
    monkeypatch.setattr(experiment, operation_name, forged)

    with pytest.raises(ValueError, match="loaded operation"):
        SourceAttestedOperation(
            operation=forged,
            source_tree=capture_source_tree(),
            expected_module_name=experiment.__name__,
            expected_function_name=operation_name,
        )


def test_source_attested_operation_rejects_alternate_loaded_module_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import experiment
    from yieldforge.oracle.source_attestation import (
        SourceAttestedOperation,
        capture_source_tree,
    )

    alternate = tmp_path / "alternate" / "yieldforge" / "oracle" / "experiment.py"
    alternate.parent.mkdir(parents=True)
    alternate.write_bytes(Path(experiment.__file__).read_bytes())
    monkeypatch.setattr(experiment, "__file__", str(alternate))

    with pytest.raises(ValueError, match="loaded module"):
        SourceAttestedOperation(
            operation=experiment._profile_portable_generation_worker,  # noqa: SLF001
            source_tree=capture_source_tree(),
            expected_module_name=experiment.__name__,
            expected_function_name="_profile_portable_generation_worker",
        )


def test_source_attested_operation_rejects_source_changed_during_operation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle.source_attestation import (
        SourceAttestedOperation,
        capture_source_tree,
    )

    package, primary = _write_source_tree(tmp_path)
    module = _load_attested_test_operations(package, monkeypatch)
    snapshot = capture_source_tree(package)

    operation = SourceAttestedOperation(
        operation=module.mutate_then_return,
        source_tree=snapshot,
        expected_module_name=module.__name__,
        expected_function_name="mutate_then_return",
    )

    with pytest.raises(ValueError, match="source tree changed"):
        operation(primary)


def test_source_attested_operation_checks_after_a_failing_operation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle.source_attestation import (
        SourceAttestedOperation,
        capture_source_tree,
    )

    package, primary = _write_source_tree(tmp_path)
    module = _load_attested_test_operations(package, monkeypatch)
    snapshot = capture_source_tree(package)

    operation = SourceAttestedOperation(
        operation=module.mutate_then_fail,
        source_tree=snapshot,
        expected_module_name=module.__name__,
        expected_function_name="mutate_then_fail",
    )

    with pytest.raises(ValueError, match="source tree changed") as captured:
        operation(primary)
    assert isinstance(captured.value.__cause__, RuntimeError)


def test_source_attestation_scope_uses_fresh_pycache_and_restores_environment(
    tmp_path: Path,
) -> None:
    from yieldforge.oracle.source_attestation import (
        activate_source_attestation,
        capture_source_tree,
    )

    package, _primary = _write_source_tree(tmp_path)
    snapshot = capture_source_tree(package)
    name = "PYTHONPYCACHEPREFIX"
    path_name = "PYTHONPATH"
    was_present = name in os.environ
    prior = os.environ.get(name)
    path_was_present = path_name in os.environ
    prior_pythonpath = os.environ.get(path_name)
    prior_sys_path = tuple(sys.path)

    with activate_source_attestation(snapshot) as pycache_prefix:
        assert Path(os.environ[name]) == pycache_prefix
        assert pycache_prefix.is_dir()
        assert tuple(pycache_prefix.iterdir()) == ()
        mirror_import_root = Path(sys.path[0])
        mirror_package = mirror_import_root / "yieldforge"
        assert os.environ[path_name].split(os.pathsep)[0] == str(mirror_import_root)
        assert (mirror_package / "worker.py").read_text() == "VALUE = 1\n"
        primary = package / "worker.py"
        primary.write_text("VALUE = 2\n")
        assert (mirror_package / "worker.py").read_text() == "VALUE = 1\n"
        primary.write_text("VALUE = 1\n")

    assert (name in os.environ) is was_present
    assert os.environ.get(name) == prior
    assert (path_name in os.environ) is path_was_present
    assert os.environ.get(path_name) == prior_pythonpath
    assert tuple(sys.path) == prior_sys_path


def test_spawned_attested_operation_executes_from_the_private_source_mirror() -> None:
    from yieldforge.oracle.experiment import _run_process_phase
    from yieldforge.oracle.source_attestation import (
        SourceAttestedOperation,
        activate_source_attestation,
        capture_source_tree,
    )

    snapshot = capture_source_tree()
    with activate_source_attestation(snapshot):
        mirror_package_root = Path(sys.path[0]) / "yieldforge"
        (observed,) = _run_process_phase(
            SourceAttestedOperation(
                operation=capture_source_tree,
                source_tree=snapshot,
                expected_module_name="yieldforge.oracle.source_attestation",
                expected_function_name="capture_source_tree",
            ),
            ((),),
            process_count=1,
        )
        assert observed.package_root == mirror_package_root
        assert observed.package_root != snapshot.package_root
        assert observed.source_files == snapshot.source_files


def test_source_attestation_scope_checks_source_after_a_failing_body(
    tmp_path: Path,
) -> None:
    from yieldforge.oracle.source_attestation import (
        activate_source_attestation,
        capture_source_tree,
    )

    package, primary = _write_source_tree(tmp_path)
    snapshot = capture_source_tree(package)
    name = "PYTHONPYCACHEPREFIX"
    was_present = name in os.environ
    prior = os.environ.get(name)

    with pytest.raises(ValueError, match="source tree changed") as captured:
        with activate_source_attestation(snapshot):
            primary.write_text("VALUE = 2\n")
            raise RuntimeError("controller failed")

    assert isinstance(captured.value.__cause__, RuntimeError)
    assert (name in os.environ) is was_present
    assert os.environ.get(name) == prior


def test_source_tree_identity_is_relocation_stable_and_primary_bound(
    tmp_path: Path,
) -> None:
    from yieldforge.oracle.source_attestation import (
        capture_source_tree,
        source_tree_implementation_identity,
    )

    first_package, first_primary = _write_source_tree(tmp_path / "first")
    second_package, second_primary = _write_source_tree(tmp_path / "second")
    first = source_tree_implementation_identity(
        "portable-profile",
        (first_primary,),
        source_tree=capture_source_tree(first_package),
    )
    second = source_tree_implementation_identity(
        "portable-profile",
        (second_primary,),
        source_tree=capture_source_tree(second_package),
    )

    assert first == second
    assert first[0] == "yieldforge-m8-portable-profile-v1"
    assert first[1].startswith("sha256:")
