"""Cycle-free source and bytecode-cache attestation for spawned M8 evidence."""

from __future__ import annotations

import hashlib
import marshal
import os
import stat
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from types import CodeType, FunctionType, ModuleType

from yieldforge.experiments.contracts import semantic_sha256


@dataclass(frozen=True)
class SourceFileIdentity:
    relative_path: str
    content_sha256: str


@dataclass(frozen=True)
class SourceTreeSnapshot:
    package_root: Path
    source_files: tuple[SourceFileIdentity, ...]


_SOURCE_ATTESTATION_LOCK = threading.Lock()
_SOURCE_MIRROR_ENVIRONMENT = "YIELDFORGE_M8_ATTESTED_PACKAGE_ROOT"


def _sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def capture_source_tree(package_root: Path | None = None) -> SourceTreeSnapshot:
    """Capture every regular YieldForge Python source without host-specific paths."""

    requested_root = (
        Path(__file__).resolve().parents[1]
        if package_root is None
        else Path(package_root)
    )
    try:
        root_metadata = requested_root.lstat()
    except OSError as error:
        raise ValueError("M8 YieldForge package source tree is absent") from error
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
        raise ValueError("M8 YieldForge package source tree must be a regular directory")
    root = requested_root.resolve()
    descendants = tuple(root.rglob("*"))
    if any(path.is_symlink() for path in descendants):
        raise ValueError("M8 YieldForge package source tree contains a symlink")
    sources = tuple(
        sorted(
            (path for path in descendants if path.suffix == ".py"),
            key=lambda path: path.relative_to(root).as_posix(),
        )
    )
    if not sources:
        raise ValueError("M8 YieldForge package source tree has no Python sources")

    identities = []
    for source in sources:
        before = source.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("M8 YieldForge source must be a regular file")
        content = source.read_bytes()
        after = source.lstat()
        before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if before_identity != after_identity:
            raise ValueError("M8 YieldForge source changed while being captured")
        identities.append(
            SourceFileIdentity(
                relative_path="yieldforge/" + source.relative_to(root).as_posix(),
                content_sha256=_sha256_bytes(content),
            )
        )
    return SourceTreeSnapshot(package_root=root, source_files=tuple(identities))


def require_source_tree_unchanged(expected: SourceTreeSnapshot) -> None:
    """Fail closed when spawned work may have observed different source bytes."""

    if type(expected) is not SourceTreeSnapshot:
        raise TypeError("M8 source stability requires an exact tree snapshot")
    try:
        observed = capture_source_tree(expected.package_root)
    except (OSError, ValueError) as error:
        raise ValueError("M8 YieldForge source tree changed during execution") from error
    if observed.source_files != expected.source_files:
        raise ValueError("M8 YieldForge source tree changed during execution")


def _source_identity_by_path(expected: SourceTreeSnapshot) -> dict[str, str]:
    return {
        item.relative_path: item.content_sha256
        for item in expected.source_files
    }


def _module_name_by_source_path(expected: SourceTreeSnapshot) -> dict[str, str]:
    names = {}
    for item in expected.source_files:
        path = item.relative_path
        if path == "yieldforge/__init__.py":
            module_name = "yieldforge"
        elif path.endswith("/__init__.py"):
            module_name = path[: -len("/__init__.py")].replace("/", ".")
        else:
            module_name = path[:-3].replace("/", ".")
        if module_name in names:
            raise ValueError("M8 source tree maps multiple sources to one module")
        names[module_name] = path
    return names


def _read_attested_module_source(
    module: ModuleType,
    *,
    relative_path: str,
    expected_sha256: str,
    loaded_package_root: Path,
) -> bytes:
    source_name = getattr(module, "__file__", None)
    if type(source_name) is not str:
        raise ValueError("M8 loaded module has no regular source path")
    source = Path(source_name)
    try:
        before = source.lstat()
        resolved = source.resolve(strict=True)
        observed_relative = "yieldforge/" + resolved.relative_to(
            loaded_package_root.resolve(strict=True)
        ).as_posix()
    except (OSError, ValueError) as error:
        raise ValueError("M8 loaded module is outside the attested source root") from error
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ValueError("M8 loaded module source must be a regular file")
    if observed_relative != relative_path:
        raise ValueError("M8 loaded module path differs from the attested source")
    spec = getattr(module, "__spec__", None)
    origin = getattr(spec, "origin", None)
    if type(origin) is not str:
        raise ValueError("M8 loaded module has no import origin")
    try:
        resolved_origin = Path(origin).resolve(strict=True)
    except OSError as error:
        raise ValueError("M8 loaded module import origin is absent") from error
    if resolved_origin != resolved:
        raise ValueError("M8 loaded module path differs from its import origin")
    content = source.read_bytes()
    after = source.lstat()
    before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if before_identity != after_identity:
        raise ValueError("M8 loaded module source changed while being checked")
    if _sha256_bytes(content) != expected_sha256:
        raise ValueError("M8 loaded module source differs from the attested source")
    return content


def _normalized_code(value: CodeType) -> CodeType:
    constants = tuple(
        _normalized_code(item) if type(item) is CodeType else item
        for item in value.co_consts
    )
    return value.replace(
        co_consts=constants,
        co_filename="<yieldforge-attested-source>",
    )


def _code_sha256(value: CodeType) -> str:
    return _sha256_bytes(marshal.dumps(_normalized_code(value)))


def _nested_code_objects(value: CodeType) -> Iterator[CodeType]:
    yield value
    for item in value.co_consts:
        if type(item) is CodeType:
            yield from _nested_code_objects(item)


@dataclass(frozen=True)
class _LoadedOperationIdentity:
    module_name: str
    function_name: str
    qualified_name: str
    relative_path: str
    source_content_sha256: str
    code_content_sha256: str


def _loaded_operation_identity(
    operation: object,
    *,
    source_tree: SourceTreeSnapshot,
    loaded_package_root: Path,
) -> _LoadedOperationIdentity:
    if type(operation) is not FunctionType:
        raise ValueError("M8 loaded operation must be an exact Python function")
    module_name = operation.__module__
    function_name = operation.__name__
    qualified_name = operation.__qualname__
    if (
        type(module_name) is not str
        or not (module_name == "yieldforge" or module_name.startswith("yieldforge."))
        or type(function_name) is not str
        or type(qualified_name) is not str
        or qualified_name != function_name
    ):
        raise ValueError("M8 loaded operation is not a top-level YieldForge function")
    module = sys.modules.get(module_name)
    if type(module) is not ModuleType:
        raise ValueError("M8 loaded operation module is absent")
    if getattr(module, function_name, None) is not operation:
        raise ValueError("M8 loaded operation is not its module's exported function")
    if operation.__globals__ is not module.__dict__:
        raise ValueError("M8 loaded operation globals differ from its module")

    module_paths = _module_name_by_source_path(source_tree)
    relative_path = module_paths.get(module_name)
    if relative_path is None:
        raise ValueError("M8 loaded operation module is absent from the attested source")
    expected_sha256 = _source_identity_by_path(source_tree)[relative_path]
    source = _read_attested_module_source(
        module,
        relative_path=relative_path,
        expected_sha256=expected_sha256,
        loaded_package_root=loaded_package_root,
    )
    compiled = compile(
        source,
        relative_path,
        "exec",
        dont_inherit=True,
        optimize=sys.flags.optimize,
    )
    candidates = tuple(
        item
        for item in _nested_code_objects(compiled)
        if item.co_qualname == qualified_name
        and item.co_firstlineno == operation.__code__.co_firstlineno
    )
    if len(candidates) != 1 or _code_sha256(candidates[0]) != _code_sha256(
        operation.__code__
    ):
        raise ValueError("M8 loaded operation code differs from the attested source")
    return _LoadedOperationIdentity(
        module_name=module_name,
        function_name=function_name,
        qualified_name=qualified_name,
        relative_path=relative_path,
        source_content_sha256=expected_sha256,
        code_content_sha256=_code_sha256(candidates[0]),
    )


def _require_loaded_yieldforge_modules(
    expected: SourceTreeSnapshot,
    *,
    loaded_package_root: Path,
) -> None:
    source_by_path = _source_identity_by_path(expected)
    for module_name, relative_path in _module_name_by_source_path(expected).items():
        module = sys.modules.get(module_name)
        if module is None:
            continue
        if type(module) is not ModuleType:
            raise ValueError("M8 loaded module registry entry is malformed")
        _read_attested_module_source(
            module,
            relative_path=relative_path,
            expected_sha256=source_by_path[relative_path],
            loaded_package_root=loaded_package_root,
        )


def _require_loaded_operation(
    operation: object,
    expected_identity: _LoadedOperationIdentity,
    *,
    source_tree: SourceTreeSnapshot,
    loaded_package_root: Path,
) -> None:
    _require_loaded_yieldforge_modules(
        source_tree,
        loaded_package_root=loaded_package_root,
    )
    observed = _loaded_operation_identity(
        operation,
        source_tree=source_tree,
        loaded_package_root=loaded_package_root,
    )
    if observed != expected_identity:
        raise ValueError("M8 loaded operation identity differs from its issuance")


@dataclass(frozen=True)
class SourceAttestedOperation:
    """Pickle-safe worker wrapper that brackets execution with source checks."""

    operation: object
    source_tree: SourceTreeSnapshot
    expected_module_name: str
    expected_function_name: str
    _operation_identity: _LoadedOperationIdentity = field(init=False, repr=False)
    _worker_package_root: Path = field(init=False, repr=False)
    _issuing_pid: int = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self.source_tree) is not SourceTreeSnapshot:
            raise TypeError("M8 source-attested operation requires an exact tree snapshot")
        if (
            type(self.expected_module_name) is not str
            or not self.expected_module_name.startswith("yieldforge.")
            or type(self.expected_function_name) is not str
            or not self.expected_function_name
        ):
            raise TypeError("M8 source-attested operation target is malformed")
        if (
            type(self.operation) is not FunctionType
            or self.operation.__module__ != self.expected_module_name
            or self.operation.__name__ != self.expected_function_name
            or self.operation.__qualname__ != self.expected_function_name
        ):
            raise ValueError("M8 loaded operation differs from the declared target")
        require_source_tree_unchanged(self.source_tree)
        _require_loaded_yieldforge_modules(
            self.source_tree,
            loaded_package_root=self.source_tree.package_root,
        )
        identity = _loaded_operation_identity(
            self.operation,
            source_tree=self.source_tree,
            loaded_package_root=self.source_tree.package_root,
        )
        worker_root_name = os.environ.get(_SOURCE_MIRROR_ENVIRONMENT)
        worker_root = (
            self.source_tree.package_root
            if worker_root_name is None
            else Path(worker_root_name).resolve(strict=True)
        )
        object.__setattr__(self, "_operation_identity", identity)
        object.__setattr__(self, "_worker_package_root", worker_root)
        object.__setattr__(self, "_issuing_pid", os.getpid())

    def __call__(self, *args):  # type: ignore[no-untyped-def]
        require_source_tree_unchanged(self.source_tree)
        loaded_package_root = (
            self.source_tree.package_root
            if os.getpid() == self._issuing_pid
            else self._worker_package_root
        )
        _require_loaded_operation(
            self.operation,
            self._operation_identity,
            source_tree=self.source_tree,
            loaded_package_root=loaded_package_root,
        )
        try:
            result = self.operation(*args)  # type: ignore[operator]
        except BaseException as operation_error:
            try:
                require_source_tree_unchanged(self.source_tree)
                _require_loaded_operation(
                    self.operation,
                    self._operation_identity,
                    source_tree=self.source_tree,
                    loaded_package_root=loaded_package_root,
                )
            except BaseException as source_error:
                raise source_error from operation_error
            raise
        require_source_tree_unchanged(self.source_tree)
        _require_loaded_operation(
            self.operation,
            self._operation_identity,
            source_tree=self.source_tree,
            loaded_package_root=loaded_package_root,
        )
        return result


def _source_mirror_parent(package_root: Path) -> Path:
    parent = package_root.parent
    return parent.parent if parent.name == "src" else parent


def _materialize_source_mirror(
    expected: SourceTreeSnapshot,
    mirror_package_root: Path,
) -> None:
    mirror_package_root.mkdir(parents=True)
    for item in expected.source_files:
        relative = Path(item.relative_path).relative_to("yieldforge")
        source = expected.package_root / relative
        before = source.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise ValueError("M8 YieldForge source must be a regular file")
        content = source.read_bytes()
        after = source.lstat()
        before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if before_identity != after_identity or _sha256_bytes(content) != item.content_sha256:
            raise ValueError("M8 YieldForge source changed while mirror was captured")
        destination = mirror_package_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        destination.chmod(0o444)
    observed = capture_source_tree(mirror_package_root)
    if observed.source_files != expected.source_files:
        raise ValueError("M8 YieldForge source mirror differs from the captured tree")


@contextmanager
def activate_source_attestation(
    expected: SourceTreeSnapshot,
) -> Iterator[Path]:
    """Give all scoped spawns one fresh bytecode cache and stable source tree."""

    if type(expected) is not SourceTreeSnapshot:
        raise TypeError("M8 source-attested scope requires an exact tree snapshot")
    if not _SOURCE_ATTESTATION_LOCK.acquire(blocking=False):
        raise RuntimeError("M8 source-attested spawn scope is already active")
    pycache_environment = "PYTHONPYCACHEPREFIX"
    pythonpath_environment = "PYTHONPATH"
    prior_environment = {
        name: (name in os.environ, os.environ.get(name))
        for name in (
            pycache_environment,
            pythonpath_environment,
            _SOURCE_MIRROR_ENVIRONMENT,
        )
    }
    prior_sys_path = tuple(sys.path)
    try:
        require_source_tree_unchanged(expected)
        mirror_parent = _source_mirror_parent(expected.package_root)
        with TemporaryDirectory(
            prefix=".yieldforge-m8-attested-",
            dir=mirror_parent,
        ) as mirror_temporary:
            mirror_import_root = Path(mirror_temporary).resolve()
            mirror_package_root = mirror_import_root / "yieldforge"
            _materialize_source_mirror(expected, mirror_package_root)
            with TemporaryDirectory(prefix="yieldforge-m8-profile-pycache-") as temporary:
                pycache_prefix = Path(temporary).resolve()
                os.environ[pycache_environment] = str(pycache_prefix)
                prior_pythonpath = prior_environment[pythonpath_environment][1]
                os.environ[pythonpath_environment] = str(mirror_import_root) + (
                    ""
                    if not prior_pythonpath
                    else os.pathsep + prior_pythonpath
                )
                os.environ[_SOURCE_MIRROR_ENVIRONMENT] = str(mirror_package_root)
                sys.path.insert(0, str(mirror_import_root))
                try:
                    try:
                        yield pycache_prefix
                    except BaseException as operation_error:
                        try:
                            require_source_tree_unchanged(expected)
                            mirror = capture_source_tree(mirror_package_root)
                            if mirror.source_files != expected.source_files:
                                raise ValueError(
                                    "M8 YieldForge source mirror changed during execution"
                                )
                        except BaseException as source_error:
                            raise source_error from operation_error
                        raise
                    require_source_tree_unchanged(expected)
                    mirror = capture_source_tree(mirror_package_root)
                    if mirror.source_files != expected.source_files:
                        raise ValueError("M8 YieldForge source mirror changed during execution")
                finally:
                    sys.path[:] = prior_sys_path
                    for name, (was_present, prior_value) in prior_environment.items():
                        if was_present:
                            assert prior_value is not None
                            os.environ[name] = prior_value
                        else:
                            os.environ.pop(name, None)
    finally:
        _SOURCE_ATTESTATION_LOCK.release()


def source_tree_implementation_identity(
    role: str,
    source_paths: tuple[Path, ...],
    *,
    source_tree: SourceTreeSnapshot,
) -> tuple[str, str]:
    """Bind named primary sources to the complete captured YieldForge tree."""

    if type(role) is not str or not role:
        raise TypeError("M8 implementation role must be a nonempty string")
    if type(source_paths) is not tuple or not source_paths:
        raise ValueError("M8 implementation identity requires a primary source")
    if type(source_tree) is not SourceTreeSnapshot:
        raise TypeError("M8 implementation identity requires an exact tree snapshot")
    available = {item.relative_path for item in source_tree.source_files}
    primary_sources = set()
    for item in source_paths:
        source = Path(item)
        try:
            metadata = source.lstat()
            resolved = source.resolve(strict=True)
            relative = resolved.relative_to(source_tree.package_root).as_posix()
        except (OSError, ValueError) as error:
            raise ValueError("M8 primary implementation is outside the source tree") from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ValueError("M8 primary implementation must be a regular source file")
        relative_path = f"yieldforge/{relative}"
        if relative_path not in available:
            raise ValueError("M8 primary implementation is absent from the source tree")
        primary_sources.add(relative_path)
    digest = semantic_sha256(
        {
            "schema_version": "yieldforge.m8-source-tree-identity.v1",
            "role": role,
            "primary_sources": tuple(sorted(primary_sources)),
            "source_files": tuple(
                {
                    "path": item.relative_path,
                    "content_sha256": item.content_sha256,
                }
                for item in source_tree.source_files
            ),
        }
    )
    return f"yieldforge-m8-{role}-v1", f"sha256:{digest}"


__all__ = [
    "SourceAttestedOperation",
    "SourceFileIdentity",
    "SourceTreeSnapshot",
    "activate_source_attestation",
    "capture_source_tree",
    "require_source_tree_unchanged",
    "source_tree_implementation_identity",
]
