"""Executed, calibration-only evidence harness for the complete M8 Gate 3."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import stat
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import ValidationError

from yieldforge.baseline.archives import (
    canonical_m2_archive_references,
    verify_problem_candidates,
)
from yieldforge.baseline.contracts import M7CalibrationProblemView
from yieldforge.baseline.experiment import M7FrozenBaseline, select_calibration_instances
from yieldforge.experiments.contracts import M0ExperimentContract, semantic_sha256
from yieldforge.oracle import checker as checker_module
from yieldforge.oracle import experiment as experiment_module
from yieldforge.oracle import fact_checker as fact_checker_module
from yieldforge.oracle import facts as facts_module
from yieldforge.oracle import gate3_evidence as gate3_evidence_module
from yieldforge.oracle import gate3_normalization as normalization_module
from yieldforge.oracle import reference as reference_module
from yieldforge.oracle import sparse as sparse_module
from yieldforge.oracle.artifact_publisher import (
    M8ArtifactPublicationError,
    publish_immutable_artifact,
)
from yieldforge.oracle.checker import M8ProofCheckResult, check_action_proofs
from yieldforge.oracle.concurrency import (
    M8_GATE3_CONCURRENCY_BUDGET,
    activate_m8_translation_audit_processes,
)
from yieldforge.oracle.contracts import M8ActionScore
from yieldforge.oracle.experiment import (
    M8CertificateProofResult,
    M8PortableFactGate3Result,
)
from yieldforge.oracle.facts import (
    M8UncheckedFactBundleV2,
    canonical_semantic_json,
    encode_canonical_f64,
)
from yieldforge.oracle.gate3_evidence import (
    M8Gate3AuditComputationIdentity,
    M8Gate3AuditResult,
    M8Gate3AuditSample,
    M8Gate3CheckedActionRoot,
    M8Gate3CheckedV2AuditRecord,
    M8Gate3Decision,
    M8Gate3NormalizedActionRecord,
    M8Gate3ReferenceCostAttestation,
    M8Gate3ReferenceTiming,
    M8Gate3RootMembershipAttestation,
    M8Gate3RootMembershipBinding,
    M8Gate3V1CheckerAuditRecord,
    M8Gate3V1GeneratorAuditRecord,
    build_gate3_reference_timing,
    finalize_gate3_audit,
    finalize_gate3_decision,
    finalize_gate3_performance,
    freeze_gate3_audit_sample,
    freeze_gate3_checked_root_manifest,
    gate3_checked_root_sequence_sha256,
    gate3_checked_v2_output_sha256,
    normalized_gate3_action_semantic_sha256,
    require_official_portable_gate3,
)
from yieldforge.oracle.gate3_normalization import (
    normalize_m8_action_proof,
    normalize_m8_fact_bundle_root,
)
from yieldforge.oracle.proofs import M8ActionProof
from yieldforge.residuals.contracts import rule_set_from_m0
from yieldforge.temporal_benchmark.contracts import (
    TemporalRegime,
    build_registered_contract,
)


@dataclass(frozen=True)
class _Gate3CheckedCorpus:
    roots: tuple[M8Gate3CheckedActionRoot, ...]
    bundles: tuple[M8UncheckedFactBundleV2, M8UncheckedFactBundleV2]
    sources: tuple[
        experiment_module._PortableFactCheckedSource,
        experiment_module._PortableFactCheckedSource,
    ]


@dataclass(frozen=True)
class _Gate3V1CheckerActionOutput:
    proof: M8ActionProof
    check: M8ProofCheckResult
    checked_semantic_sha256: str
    checked_final_net_cost_bits: str
    output_content_sha256: str


@dataclass(frozen=True)
class _Gate3V1CheckerWorkerResult:
    regime: TemporalRegime
    actions: tuple[_Gate3V1CheckerActionOutput, ...]


@dataclass(frozen=True)
class _Gate3CheckedV2ActionOutput:
    root_fact_sha256: str
    checker_result_content_sha256: str
    checker_decision_id: str
    checker_decision_content_sha256: str
    checked_action_root_count: int
    checker_failure_code: str
    normalized: M8Gate3NormalizedActionRecord
    output_content_sha256: str


@dataclass(frozen=True)
class _Gate3CheckedV2WorkerResult:
    regime: TemporalRegime
    actions: tuple[_Gate3CheckedV2ActionOutput, ...]


@dataclass(frozen=True)
class _Gate3ReferenceWorkerResult:
    regime: TemporalRegime
    catalog_action_id: str
    score: M8ActionScore
    worker_seconds: float
    output_content_sha256: str


@dataclass(frozen=True)
class _Gate3SourceFileIdentity:
    relative_path: str
    content_sha256: str


@dataclass(frozen=True)
class _Gate3SourceTreeSnapshot:
    package_root: Path
    source_files: tuple[_Gate3SourceFileIdentity, ...]


_GATE3_SOURCE_ATTESTED_SPAWN_LOCK = threading.Lock()


def _sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _capture_yieldforge_source_tree(
    package_root: Path | None = None,
) -> _Gate3SourceTreeSnapshot:
    """Capture all regular YieldForge Python sources without host-specific paths."""

    requested_root = (
        Path(__file__).resolve().parents[1]
        if package_root is None
        else Path(package_root)
    )
    try:
        root_metadata = requested_root.lstat()
    except OSError as error:
        raise ValueError("M8 Gate-3 YieldForge package source tree is absent") from error
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
        raise ValueError("M8 Gate-3 YieldForge package source tree must be a regular directory")
    root = requested_root.resolve()

    descendants = tuple(root.rglob("*"))
    if any(path.is_symlink() for path in descendants):
        raise ValueError("M8 Gate-3 YieldForge package source tree contains a symlink")
    sources = tuple(
        sorted(
            (path for path in descendants if path.suffix == ".py"),
            key=lambda path: path.relative_to(root).as_posix(),
        )
    )
    if not sources:
        raise ValueError("M8 Gate-3 YieldForge package source tree has no Python sources")

    identities = []
    for source in sources:
        before = source.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("M8 Gate-3 YieldForge source must be a regular file")
        content = source.read_bytes()
        after = source.lstat()
        before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if before_identity != after_identity:
            raise ValueError("M8 Gate-3 YieldForge source changed while being captured")
        identities.append(
            _Gate3SourceFileIdentity(
                relative_path=(
                    "yieldforge/" + source.relative_to(root).as_posix()
                ),
                content_sha256=_sha256_bytes(content),
            )
        )
    return _Gate3SourceTreeSnapshot(
        package_root=root,
        source_files=tuple(identities),
    )


def _require_yieldforge_source_tree_unchanged(
    expected: _Gate3SourceTreeSnapshot,
) -> None:
    """Fail closed when spawned work may have observed different source bytes."""

    if type(expected) is not _Gate3SourceTreeSnapshot:
        raise TypeError("M8 Gate-3 source stability requires an exact tree snapshot")
    try:
        observed = _capture_yieldforge_source_tree(expected.package_root)
    except (OSError, ValueError) as error:
        raise ValueError("M8 Gate-3 YieldForge source tree changed during execution") from error
    if observed.source_files != expected.source_files:
        raise ValueError("M8 Gate-3 YieldForge source tree changed during execution")


@dataclass(frozen=True)
class _Gate3SourceAttestedOperation:
    operation: object
    source_tree: _Gate3SourceTreeSnapshot

    def __call__(self, *args):  # type: ignore[no-untyped-def]
        _require_yieldforge_source_tree_unchanged(self.source_tree)
        return self.operation(*args)  # type: ignore[operator]


@contextmanager
def _activate_gate3_source_attested_spawns(
    expected: _Gate3SourceTreeSnapshot,
    *,
    mutations_module,  # type: ignore[no-untyped-def]
) -> Iterator[Path]:
    """Attest every Gate-3 process-phase worker from a fresh bytecode cache."""

    if type(expected) is not _Gate3SourceTreeSnapshot:
        raise TypeError("M8 Gate-3 attested spawns require an exact source tree snapshot")
    if not _GATE3_SOURCE_ATTESTED_SPAWN_LOCK.acquire(blocking=False):
        raise RuntimeError("M8 Gate-3 source-attested spawn scope is already active")
    original_experiment_runner = experiment_module._run_process_phase
    original_mutation_runner = mutations_module._run_process_phase
    if original_mutation_runner is not original_experiment_runner:
        _GATE3_SOURCE_ATTESTED_SPAWN_LOCK.release()
        raise RuntimeError("M8 Gate-3 process-phase runners differ before attestation")

    def run_attested(operation, tasks, **kwargs):  # type: ignore[no-untyped-def]
        return original_experiment_runner(
            _Gate3SourceAttestedOperation(
                operation=operation,
                source_tree=expected,
            ),
            tasks,
            **kwargs,
        )

    environment_name = "PYTHONPYCACHEPREFIX"
    environment_present = environment_name in os.environ
    prior_environment = os.environ.get(environment_name)
    try:
        with TemporaryDirectory(prefix="yieldforge-m8-gate3-pycache-") as temporary:
            pycache_prefix = Path(temporary).resolve()
            os.environ[environment_name] = str(pycache_prefix)
            experiment_module._run_process_phase = run_attested
            mutations_module._run_process_phase = run_attested
            try:
                yield pycache_prefix
            finally:
                experiment_module._run_process_phase = original_experiment_runner
                mutations_module._run_process_phase = original_mutation_runner
                if environment_present:
                    assert prior_environment is not None
                    os.environ[environment_name] = prior_environment
                else:
                    os.environ.pop(environment_name, None)
    finally:
        _GATE3_SOURCE_ATTESTED_SPAWN_LOCK.release()


def _implementation_identity(
    role: str,
    source_paths: tuple[Path, ...],
    *,
    source_tree: _Gate3SourceTreeSnapshot | None = None,
) -> tuple[str, str]:
    """Bind one primary implementation to the complete YieldForge source tree."""

    if type(role) is not str or not role:
        raise TypeError("M8 Gate-3 implementation role must be a nonempty string")
    if type(source_paths) is not tuple or not source_paths:
        raise ValueError("M8 Gate-3 implementation requires a primary source")
    snapshot = (
        _capture_yieldforge_source_tree()
        if source_tree is None
        else source_tree
    )
    if type(snapshot) is not _Gate3SourceTreeSnapshot:
        raise TypeError("M8 Gate-3 implementation requires an exact source tree snapshot")
    available = {item.relative_path for item in snapshot.source_files}
    primary_sources = set()
    for item in source_paths:
        source = Path(item)
        try:
            metadata = source.lstat()
            resolved = source.resolve(strict=True)
            relative = resolved.relative_to(snapshot.package_root).as_posix()
        except (OSError, ValueError) as error:
            raise ValueError(
                "M8 Gate-3 primary implementation is outside the package source tree"
            ) from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ValueError("M8 Gate-3 primary implementation must be a regular source file")
        relative_path = f"yieldforge/{relative}"
        if relative_path not in available:
            raise ValueError(
                "M8 Gate-3 primary implementation is absent from the package source tree"
            )
        primary_sources.add(relative_path)
    digest = semantic_sha256(
        {
            "schema_version": "yieldforge.m8-gate3-source-tree-identity.v2",
            "primary_sources": tuple(sorted(primary_sources)),
            "source_files": tuple(
                {
                    "path": item.relative_path,
                    "content_sha256": item.content_sha256,
                }
                for item in snapshot.source_files
            ),
        }
    )
    return f"yieldforge-m8-gate3-{role}-v2", f"sha256:{digest}"


def _runtime_identity(*, jagua_executable_sha256: str) -> tuple[str, str]:
    """Bind spawned evidence to the relevant interpreter and native runtime."""

    packages = {}
    for name in ("pydantic", "shapely"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError as error:
            raise ValueError(f"M8 Gate-3 runtime package is absent: {name}") from error
    digest = semantic_sha256(
        {
            "schema_version": "yieldforge.m8-gate3-runtime-identity.v1",
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "python_executable_name": Path(sys.executable).name,
            "platform_machine": platform.machine(),
            "packages": packages,
            "jagua_executable_sha256": jagua_executable_sha256,
        }
    )
    return "yieldforge-m8-gate3-runtime-v1", f"sha256:{digest}"


def load_portable_fact_gate3(path: Path) -> M8PortableFactGate3Result:
    """Strict-load one immutable portable-pipeline result."""

    source = Path(path)
    if not source.is_file():
        raise ValueError("M8 portable Gate-3 artifact is absent")
    try:
        parsed = M8PortableFactGate3Result.model_validate_json(
            source.read_bytes(),
            strict=True,
        )
        return require_official_portable_gate3(
            parsed,
            label="M8 portable Gate-3 artifact",
        )
    except (OSError, ValidationError, TypeError, ValueError) as error:
        raise ValueError("M8 portable Gate-3 artifact failed strict validation") from error


def _prepare_gate3_execution_cells(
    *,
    index: M7CalibrationProblemView,
    m0: M0ExperimentContract,
    frozen: M7FrozenBaseline,
    portable_fact_gate3: M8PortableFactGate3Result,
    archive_roots: tuple[Path, ...],
    jagua_executable: Path,
):  # type: ignore[no-untyped-def]
    """Rebuild only the two sealed calibration cells used by portable Gate 3."""

    if (
        type(index) is not M7CalibrationProblemView
        or type(m0) is not M0ExperimentContract
        or type(frozen) is not M7FrozenBaseline
        or type(portable_fact_gate3) is not M8PortableFactGate3Result
    ):
        raise TypeError("M8 Gate-3 execution requires exact frozen input contracts")
    index = M7CalibrationProblemView.model_validate_json(
        index.model_dump_json(), strict=True
    )
    m0 = M0ExperimentContract.model_validate_json(m0.model_dump_json(), strict=True)
    frozen = M7FrozenBaseline.model_validate_json(
        frozen.model_dump_json(), strict=True
    )
    gate3 = require_official_portable_gate3(
        portable_fact_gate3,
        label="M8 Gate-3 execution",
    )
    contract = build_registered_contract()
    expected_lineage = (
        gate3.m0_contract_id,
        gate3.m0_contract_sha256,
        gate3.m6_contract_id,
        gate3.m6_contract_sha256,
        gate3.m6_population_id,
        gate3.m6_population_sha256,
        gate3.problem_index_id,
        gate3.problem_index_sha256,
        gate3.freeze_id,
        gate3.freeze_sha256,
        gate3.calibration_view_id,
        gate3.calibration_view_sha256,
    )
    observed_lineage = (
        m0.contract_id,
        m0.content_sha256,
        index.m6_contract_id,
        index.m6_contract_sha256,
        index.m6_population_id,
        index.m6_population_sha256,
        index.full_problem_index_id,
        index.full_problem_index_sha256,
        frozen.freeze_id,
        frozen.content_sha256,
        index.view_id,
        index.content_sha256,
    )
    if (
        expected_lineage != observed_lineage
        or (index.full_problem_index_id, index.full_problem_index_sha256)
        != (frozen.problem_index_id, frozen.problem_index_sha256)
        or (m0.contract_id, m0.content_sha256)
        != (frozen.m0_contract_id, frozen.m0_contract_sha256)
        or (index.m6_contract_id, index.m6_contract_sha256)
        != (contract.contract_id, contract.content_sha256)
        or index.evaluation_partition_opened
    ):
        raise ValueError("M8 Gate-3 execution inputs differ from the sealed lineage")

    executable = Path(jagua_executable)
    metadata = executable.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("M8 Gate-3 Jagua runtime must be a regular file")
    executable_sha = _sha256_bytes(executable.read_bytes())
    if executable_sha != frozen.runtime.jagua_executable_sha256:
        raise ValueError("M8 Gate-3 Jagua runtime differs from the M7 freeze")

    selected_streams = experiment_module._select_portable_gate3_probe_streams(index)
    calibration = select_calibration_instances(index)
    problem_by_id = {item.problem_id: item for item in index.problems}
    selected_problem_ids = tuple(
        sorted({item.problem_id for stream in selected_streams for item in stream})
    )
    references_by_task: dict[int, list[object]] = {}
    for reference in canonical_m2_archive_references():
        references_by_task.setdefault(reference.tasks_index, []).append(reference)
    verified = {}
    for problem_id in selected_problem_ids:
        problem = problem_by_id[problem_id]
        verified[problem_id] = verify_problem_candidates(
            problem,
            tuple(references_by_task[problem.tasks_index]),  # type: ignore[arg-type]
            archive_roots,
        )
    calibration_problem_ids = tuple(sorted({item.problem_id for item in calibration}))
    frozen_by_problem = {
        problem_id: (candidate_id, candidate_sha)
        for problem_id, candidate_id, candidate_sha in zip(
            calibration_problem_ids,
            frozen.candidate_set_ids,
            frozen.candidate_set_sha256s,
            strict=True,
        )
    }
    for problem_id, candidates in verified.items():
        if (
            candidates.evidence.candidate_set_id,
            candidates.evidence.content_sha256,
        ) != frozen_by_problem[problem_id]:
            raise ValueError("M8 Gate-3 candidates differ from the M7 freeze")
    cells = experiment_module._build_execution_cells(
        index=index,
        m0=m0,
        frozen=frozen,
        verified=verified,
        selected_streams=list(selected_streams),
    )
    return (
        cells,
        rule_set_from_m0(m0.remnant_eligibility),
        executable,
        executable_sha,
    )


def _strict_canonical_bundle(semantic_bytes: bytes) -> M8UncheckedFactBundleV2:
    try:
        bundle = M8UncheckedFactBundleV2.model_validate_json(
            semantic_bytes,
            strict=True,
        )
    except (ValidationError, TypeError, ValueError) as error:
        raise ValueError("M8 Gate-3 retained source is not a strict fact bundle") from error
    canonical = canonical_semantic_json(bundle.model_dump(mode="json"))
    if canonical != semantic_bytes:
        raise ValueError("M8 Gate-3 retained source bytes are noncanonical")
    return bundle


def _extract_gate3_checked_corpus(
    portable_fact_gate3: M8PortableFactGate3Result,
    retained_sources: tuple[experiment_module._PortableFactCheckedSource, ...],
) -> _Gate3CheckedCorpus:
    """Extract compact roots only from bytes accepted by the fresh checker."""

    gate3 = require_official_portable_gate3(
        portable_fact_gate3,
        label="M8 Gate-3 retained source extraction",
    )
    if len(retained_sources) != 2 or any(
        type(item) is not experiment_module._PortableFactCheckedSource
        for item in retained_sources
    ):
        raise ValueError("M8 Gate-3 retained source capture requires exactly two cells")
    by_regime = {item.first_generation.regime: item for item in retained_sources}
    if set(by_regime) != {TemporalRegime.NO_SIGNAL, TemporalRegime.REGIME_SHIFT}:
        raise ValueError("M8 Gate-3 retained source regimes differ from the freeze")

    roots: list[M8Gate3CheckedActionRoot] = []
    bundles: list[M8UncheckedFactBundleV2] = []
    ordered_sources = tuple(by_regime[cell.regime] for cell in gate3.cells)
    for cell, source in zip(gate3.cells, ordered_sources, strict=True):
        generated = source.first_generation
        checked = source.check
        bundle = _strict_canonical_bundle(source.semantic_bundle_bytes)
        decision = checked.check.decision
        layer_counts = (
            len(bundle.translation_batches),
            len(bundle.candidate_scalar_facts),
            len(bundle.frontier_facts),
            len(bundle.standard_candidate_facts),
            len(bundle.common_lemmas),
            len(bundle.influence_facts),
            len(bundle.action_roots),
        )
        if decision is None or not checked.check.valid:
            raise ValueError("M8 Gate-3 retained source lacks a valid checked decision")
        if (
            source.cell_identity
            != (cell.regime, cell.temporal_seed, cell.stream_id, cell.event_count)
            or generated.semantic_bundle_bytes_sha256
            != cell.first_semantic_bundle_bytes_sha256
            or generated.bundle_sha256 != cell.first_bundle_sha256
            or _sha256_bytes(source.semantic_bundle_bytes)
            != cell.first_semantic_bundle_bytes_sha256
            or bundle.bundle_sha256 != cell.first_bundle_sha256
            or len(source.semantic_bundle_bytes) != cell.semantic_serialized_bytes
            or layer_counts
            != (
                cell.translation_batch_count,
                cell.candidate_scalar_fact_count,
                cell.frontier_fact_count,
                cell.standard_candidate_fact_count,
                cell.common_lemma_count,
                cell.influence_fact_count,
                cell.generated_action_root_count,
            )
            or checked.check.checked_action_root_count
            != cell.checked_action_root_count
            or (decision.decision_id, decision.content_sha256)
            != (cell.decision_id, cell.decision_content_sha256)
            or bundle.provenance.regime != cell.regime.value
            or bundle.provenance.temporal_seed != cell.temporal_seed
            or bundle.provenance.stream_id != cell.stream_id
            or bundle.provenance.evaluation_partition_opened
        ):
            raise ValueError(
                "M8 Gate-3 retained checked source differs from the published cell"
            )
        root_action_ids = tuple(root.catalog_action_id for root in bundle.action_roots)
        decision_action_ids = tuple(item.action_id for item in decision.scores)
        if (
            len(root_action_ids) != len(set(root_action_ids))
            or len(decision_action_ids) != len(set(decision_action_ids))
            or set(root_action_ids) != set(decision_action_ids)
        ):
            raise ValueError("M8 Gate-3 retained roots differ from the decision")
        for root in bundle.action_roots:
            roots.append(
                M8Gate3CheckedActionRoot(
                    regime=cell.regime,
                    temporal_seed=cell.temporal_seed,
                    stream_id=cell.stream_id,
                    source_bundle_sha256=cell.first_bundle_sha256,
                    checker_decision_id=cell.decision_id,
                    checker_decision_content_sha256=cell.decision_content_sha256,
                    root_fact_sha256=root.fact_sha256,
                    action_id=root.action_id,
                    catalog_action_id=root.catalog_action_id,
                    baseline_action_id=root.baseline_action_id,
                    baseline_catalog_action_id=root.baseline_catalog_action_id,
                    start_event_position=root.start_event_position,
                    stop_event_position=root.stop_event_position,
                    suffix_sha256=root.suffix_sha256,
                    semantic_runtime_sha256=root.semantic_runtime_sha256,
                    start_state_sha256=root.start_state_sha256,
                    initial_state_after_sha256=root.initial_state_after_sha256,
                    final_state_sha256=root.final_state_sha256,
                )
            )
        bundles.append(bundle)
    if len(roots) != 887 or len(bundles) != 2:
        raise ValueError("M8 Gate-3 retained corpus differs from 887 checked roots")
    return _Gate3CheckedCorpus(
        roots=tuple(roots),
        bundles=(bundles[0], bundles[1]),
        sources=(ordered_sources[0], ordered_sources[1]),
    )


def _gate3_v1_checker_worker(
    cell,  # type: ignore[no-untyped-def]
    rules,  # type: ignore[no-untyped-def]
    jagua_executable: Path,
    proofs: tuple[M8ActionProof, ...],
    translation_audit_processes: int,
) -> _Gate3V1CheckerWorkerResult:
    """Independently check and normalize one exact v1 proof shard."""

    if not proofs or len({item.catalog_action_id for item in proofs}) != len(proofs):
        raise ValueError("M8 Gate-3 v1 checker shard is empty or duplicate")
    with activate_m8_translation_audit_processes(translation_audit_processes):
        request = experiment_module._request_for_cell(
            cell,
            rules=rules,
            jagua_executable=jagua_executable,
        )
        checks = check_action_proofs(request, proofs)
    if len(checks) != len(proofs):
        raise ValueError("M8 Gate-3 v1 checker returned a different proof count")
    outputs = []
    for proof, check in zip(proofs, checks, strict=True):
        normalized = normalize_m8_action_proof(proof)
        checked_semantic_sha256 = normalized_gate3_action_semantic_sha256(normalized)
        checked_final_net_cost_bits = normalized.final_net_cost_bits
        digest = semantic_sha256(
            {
                "schema_version": "yieldforge.m8-gate3-v1-checker-output.v1",
                "proof_id": proof.proof_id,
                "proof_content_sha256": proof.content_sha256,
                "check": check.model_dump(mode="json"),
                "checked_semantic_sha256": checked_semantic_sha256,
                "checked_final_net_cost_bits": checked_final_net_cost_bits,
            }
        )
        outputs.append(
            _Gate3V1CheckerActionOutput(
                proof=proof,
                check=check,
                checked_semantic_sha256=checked_semantic_sha256,
                checked_final_net_cost_bits=checked_final_net_cost_bits,
                output_content_sha256=f"sha256:{digest}",
            )
        )
    return _Gate3V1CheckerWorkerResult(
        regime=cell.stream[0].regime,
        actions=tuple(outputs),
    )


def _gate3_checked_v2_worker(
    semantic_bundle_bytes: bytes,
    cell,  # type: ignore[no-untyped-def]
    rules,  # type: ignore[no-untyped-def]
    jagua_executable: Path,
    freeze_id: str,
    freeze_sha256: str,
    expected_jagua_sha256: str,
    root_fact_sha256s: tuple[str, ...],
    translation_audit_processes: int,
) -> _Gate3CheckedV2WorkerResult:
    """Fresh-check retained v2 bytes before normalizing selected roots."""

    checked = experiment_module._check_portable_fact_bundle_worker(
        semantic_bundle_bytes,
        cell,
        rules,
        jagua_executable,
        freeze_id,
        freeze_sha256,
        expected_jagua_sha256,
        translation_audit_processes,
    )
    check_result = fact_checker_module.M8CheckedFactBundleResult.model_validate_json(
        checked.check.model_dump_json(),
        strict=True,
    )
    if not check_result.valid or check_result.decision is None:
        raise ValueError("M8 Gate-3 checked-v2 audit rejected its retained bundle")
    checker_result_content_sha256 = (
        f"sha256:{semantic_sha256(check_result.model_dump(mode='json'))}"
    )
    bundle = _strict_canonical_bundle(semantic_bundle_bytes)
    available = {item.fact_sha256 for item in bundle.action_roots}
    if (
        not root_fact_sha256s
        or len(root_fact_sha256s) != len(set(root_fact_sha256s))
        or not set(root_fact_sha256s) <= available
    ):
        raise ValueError("M8 Gate-3 checked-v2 root shard differs from the bundle")
    return _Gate3CheckedV2WorkerResult(
        regime=cell.stream[0].regime,
        actions=tuple(
            _checked_v2_action_output(
                bundle,
                root_sha,
                check_result=check_result,
                checker_result_content_sha256=checker_result_content_sha256,
            )
            for root_sha in root_fact_sha256s
        ),
    )


def _checked_v2_action_output(
    bundle: M8UncheckedFactBundleV2,
    root_fact_sha256: str,
    *,
    check_result: fact_checker_module.M8CheckedFactBundleResult,
    checker_result_content_sha256: str,
) -> _Gate3CheckedV2ActionOutput:
    if not check_result.valid or check_result.decision is None:
        raise ValueError("M8 Gate-3 checked-v2 output requires a valid checker decision")
    normalized = normalize_m8_fact_bundle_root(
        bundle,
        root_fact_sha256=root_fact_sha256,
    )
    return _Gate3CheckedV2ActionOutput(
        root_fact_sha256=root_fact_sha256,
        checker_result_content_sha256=checker_result_content_sha256,
        checker_decision_id=check_result.decision.decision_id,
        checker_decision_content_sha256=check_result.decision.content_sha256,
        checked_action_root_count=check_result.checked_action_root_count,
        checker_failure_code=check_result.failure_code,
        normalized=normalized,
        output_content_sha256=gate3_checked_v2_output_sha256(
            root_fact_sha256,
            normalized,
            checker_result_content_sha256=checker_result_content_sha256,
            checker_decision_id=check_result.decision.decision_id,
            checker_decision_content_sha256=check_result.decision.content_sha256,
            checked_action_root_count=check_result.checked_action_root_count,
            checker_failure_code=check_result.failure_code,
        ),
    )


def _gate3_reference_worker(
    cell,  # type: ignore[no-untyped-def]
    rules,  # type: ignore[no-untyped-def]
    jagua_executable: Path,
    catalog_action_id: str,
) -> _Gate3ReferenceWorkerResult:
    """Run one exact reference action and content-address its cost output."""

    result = experiment_module._reference_audit_action_worker(
        cell,
        rules,
        jagua_executable,
        catalog_action_id,
    )
    digest = semantic_sha256(
        {
            "schema_version": "yieldforge.m8-gate3-reference-output.v1",
            "regime": result.regime.value,
            "catalog_action_id": catalog_action_id,
            "score": {
                "action_id": result.score.action_id,
                "final_net_cost_bits": encode_canonical_f64(
                    float(result.score.final_net_cost)
                ),
            },
        }
    )
    return _Gate3ReferenceWorkerResult(
        regime=result.regime,
        catalog_action_id=catalog_action_id,
        score=result.score,
        worker_seconds=result.elapsed_seconds,
        output_content_sha256=f"sha256:{digest}",
    )


def _module_source(module: object) -> Path:
    source = getattr(module, "__file__", None)
    if type(source) is not str:
        raise ValueError("M8 Gate-3 implementation module lacks a source file")
    return Path(source)


def _audit_computation(
    *,
    role: str,
    output_content_sha256: str,
    implementation: tuple[str, str],
    harness: tuple[str, str],
    runtime: tuple[str, str],
) -> M8Gate3AuditComputationIdentity:
    return M8Gate3AuditComputationIdentity(
        role=role,  # type: ignore[arg-type]
        implementation_id=implementation[0],
        implementation_content_sha256=implementation[1],
        harness_id=harness[0],
        harness_content_sha256=harness[1],
        runtime_id=runtime[0],
        runtime_content_sha256=runtime[1],
        output_content_sha256=output_content_sha256,
        worker_exit_code=0,
        evaluation_accessed=False,
    )


def _sample_action_shards(
    sample: M8Gate3AuditSample,
    *,
    shard_size: int = 6,
) -> tuple[tuple[TemporalRegime, tuple[str, ...]], ...]:
    if type(shard_size) is not int or shard_size <= 0:
        raise ValueError("M8 Gate-3 audit shard size must be positive")
    shards = []
    for regime in (TemporalRegime.NO_SIGNAL, TemporalRegime.REGIME_SHIFT):
        action_ids = tuple(
            item.catalog_action_id for item in sample.actions if item.regime is regime
        )
        for start in range(0, len(action_ids), shard_size):
            shards.append((regime, action_ids[start : start + shard_size]))
    if sum(len(items) for _, items in shards) != 12:
        raise ValueError("M8 Gate-3 audit shards differ from the frozen sample")
    return tuple(shards)


def _run_gate3_four_way_audit(
    *,
    sample: M8Gate3AuditSample,
    corpus: _Gate3CheckedCorpus,
    execution_cells: tuple[object, ...],
    rules,  # type: ignore[no-untyped-def]
    jagua_executable: Path,
    freeze_id: str,
    freeze_sha256: str,
    jagua_executable_sha256: str,
    source_tree: _Gate3SourceTreeSnapshot,
    progress=None,  # type: ignore[no-untyped-def]
) -> tuple[M8Gate3AuditResult, tuple[M8Gate3ReferenceTiming, ...]]:
    """Execute four distinct-primary 12-action arms in fresh process phases."""

    runtime = _runtime_identity(
        jagua_executable_sha256=jagua_executable_sha256,
    )
    harness = _implementation_identity(
        "audit-harness",
        (
            Path(__file__),
            _module_source(experiment_module),
            _module_source(gate3_evidence_module),
            _module_source(normalization_module),
        ),
        source_tree=source_tree,
    )
    implementations = {
        "v1_generator": _implementation_identity(
            "v1-generator",
            (_module_source(sparse_module),),
            source_tree=source_tree,
        ),
        "v1_checker": _implementation_identity(
            "v1-checker",
            (_module_source(checker_module),),
            source_tree=source_tree,
        ),
        "checked_v2": _implementation_identity(
            "checked-v2",
            (_module_source(fact_checker_module),),
            source_tree=source_tree,
        ),
        "reference": _implementation_identity(
            "reference",
            (_module_source(reference_module),),
            source_tree=source_tree,
        ),
    }
    cell_by_regime = {cell.stream[0].regime: cell for cell in execution_cells}
    source_by_regime = {
        item.first_generation.regime: item for item in corpus.sources
    }
    shards = _sample_action_shards(sample)
    budget = M8_GATE3_CONCURRENCY_BUDGET

    if progress is not None:
        progress("phase_start phase=gate3_v1_generator actions=12 shards=2")
    generated_results = experiment_module._run_process_phase(
        experiment_module._gate3_v1_generator_worker,
        tuple(
            (
                cell_by_regime[regime],
                rules,
                jagua_executable,
                action_ids,
                budget.translation_audit_processes_per_cell,
            )
            for regime, action_ids in shards
        ),
        process_count=min(budget.cell_phase_processes, len(shards)),
    )
    generated_by_key = {}
    proof_shards = []
    for result in generated_results:
        if type(result) is not experiment_module._SampleAuditGeneratorResult:
            raise TypeError("M8 Gate-3 v1 generator returned an unexpected result")
        proofs = tuple(item.proof for item in result.sampled)
        proof_shards.append((result.regime, proofs))
        for item in result.sampled:
            key = (result.regime, item.score.action_id)
            if key in generated_by_key:
                raise ValueError("M8 Gate-3 v1 generator duplicated an action")
            generated_by_key[key] = item.proof
    if len(generated_by_key) != 12:
        raise ValueError("M8 Gate-3 v1 generator omitted a sampled action")
    if progress is not None:
        progress("phase_complete phase=gate3_v1_generator actions=12")

    if progress is not None:
        progress("phase_start phase=gate3_v1_checker actions=12 shards=2")
    checker_results = experiment_module._run_process_phase(
        _gate3_v1_checker_worker,
        tuple(
            (
                cell_by_regime[regime],
                rules,
                jagua_executable,
                proofs,
                budget.translation_audit_processes_per_cell,
            )
            for regime, proofs in proof_shards
        ),
        process_count=min(budget.cell_phase_processes, len(proof_shards)),
    )
    checked_by_key = {}
    for result in checker_results:
        if type(result) is not _Gate3V1CheckerWorkerResult:
            raise TypeError("M8 Gate-3 v1 checker returned an unexpected result")
        for item in result.actions:
            key = (result.regime, item.proof.catalog_action_id)
            if key in checked_by_key:
                raise ValueError("M8 Gate-3 v1 checker duplicated an action")
            checked_by_key[key] = item
    if len(checked_by_key) != 12:
        raise ValueError("M8 Gate-3 v1 checker omitted a sampled action")
    if progress is not None:
        progress("phase_complete phase=gate3_v1_checker actions=12")

    if progress is not None:
        progress("phase_start phase=gate3_checked_v2 actions=12")
    v2_results = experiment_module._run_process_phase(
        _gate3_checked_v2_worker,
        tuple(
            (
                source_by_regime[regime].semantic_bundle_bytes,
                cell_by_regime[regime],
                rules,
                jagua_executable,
                freeze_id,
                freeze_sha256,
                jagua_executable_sha256,
                tuple(
                    item.root_fact_sha256
                    for item in sample.actions
                    if item.regime is regime
                ),
                budget.translation_audit_processes_per_cell,
            )
            for regime in (TemporalRegime.NO_SIGNAL, TemporalRegime.REGIME_SHIFT)
        ),
        process_count=2,
    )
    v2_by_key = {}
    for result in v2_results:
        if type(result) is not _Gate3CheckedV2WorkerResult:
            raise TypeError("M8 Gate-3 checked-v2 returned an unexpected result")
        for item in result.actions:
            key = (result.regime, item.root_fact_sha256)
            if key in v2_by_key:
                raise ValueError("M8 Gate-3 checked-v2 duplicated an action")
            v2_by_key[key] = item
    if len(v2_by_key) != 12:
        raise ValueError("M8 Gate-3 checked-v2 omitted a sampled action")
    if progress is not None:
        progress("phase_complete phase=gate3_checked_v2 actions=12")

    if progress is not None:
        progress("phase_start phase=gate3_reference actions=12 processes=6")
    reference_results = experiment_module._run_process_phase(
        _gate3_reference_worker,
        tuple(
            (
                cell_by_regime[action.regime],
                rules,
                jagua_executable,
                action.catalog_action_id,
            )
            for action in sample.actions
        ),
        process_count=budget.reference_phase_processes,
    )
    reference_by_key = {}
    for result in reference_results:
        if type(result) is not _Gate3ReferenceWorkerResult:
            raise TypeError("M8 Gate-3 reference returned an unexpected result")
        key = (result.regime, result.catalog_action_id)
        if key in reference_by_key:
            raise ValueError("M8 Gate-3 reference duplicated an action")
        reference_by_key[key] = result
    if len(reference_by_key) != 12:
        raise ValueError("M8 Gate-3 reference omitted a sampled action")
    if progress is not None:
        progress("phase_complete phase=gate3_reference actions=12")

    generator_records = []
    checker_records = []
    v2_records = []
    reference_records = []
    reference_timings = []
    for action in sample.actions:
        key = (action.regime, action.catalog_action_id)
        proof = generated_by_key[key]
        checked = checked_by_key[key]
        v2 = v2_by_key[(action.regime, action.root_fact_sha256)]
        reference = reference_by_key[key]
        generator_records.append(
            M8Gate3V1GeneratorAuditRecord(
                action=action,
                computation=_audit_computation(
                    role="v1_generator",
                    output_content_sha256=proof.content_sha256,
                    implementation=implementations["v1_generator"],
                    harness=harness,
                    runtime=runtime,
                ),
                generated_proof_id=proof.proof_id,
                generated_proof_content_sha256=proof.content_sha256,
                normalized=normalize_m8_action_proof(proof),
            )
        )
        checker_records.append(
            M8Gate3V1CheckerAuditRecord(
                action=action,
                computation=_audit_computation(
                    role="v1_checker",
                    output_content_sha256=checked.output_content_sha256,
                    implementation=implementations["v1_checker"],
                    harness=harness,
                    runtime=runtime,
                ),
                checked_proof_id=checked.proof.proof_id,
                checked_proof_content_sha256=checked.proof.content_sha256,
                checked_semantic_sha256=checked.checked_semantic_sha256,
                checked_final_net_cost_bits=checked.checked_final_net_cost_bits,
                checker_valid=checked.check.valid,
                checked_event_count=checked.check.checked_event_count,
                certificate_count=checked.check.certificate_count,
                exact_transition_count=checked.check.exact_transition_count,
                failure_code=checked.check.failure_code,
            )
        )
        v2_records.append(
            M8Gate3CheckedV2AuditRecord(
                action=action,
                computation=_audit_computation(
                    role="checked_v2",
                    output_content_sha256=v2.output_content_sha256,
                    implementation=implementations["checked_v2"],
                    harness=harness,
                    runtime=runtime,
                ),
                checker_result_content_sha256=v2.checker_result_content_sha256,
                checker_decision_id=v2.checker_decision_id,
                checker_decision_content_sha256=(
                    v2.checker_decision_content_sha256
                ),
                checked_action_root_count=v2.checked_action_root_count,
                checker_failure_code=v2.checker_failure_code,
                normalized=v2.normalized,
            )
        )
        reference_computation = _audit_computation(
            role="reference",
            output_content_sha256=reference.output_content_sha256,
            implementation=implementations["reference"],
            harness=harness,
            runtime=runtime,
        )
        reference_records.append(
            M8Gate3ReferenceCostAttestation(
                computation=reference_computation,
                regime=action.regime,
                action_id=action.action_id,
                catalog_action_id=action.catalog_action_id,
                root_fact_sha256=action.root_fact_sha256,
                final_net_cost_bits=encode_canonical_f64(
                    float(reference.score.final_net_cost)
                ),
            )
        )
        reference_timings.append(
            build_gate3_reference_timing(
                regime=action.regime,
                action_id=action.action_id,
                catalog_action_id=action.catalog_action_id,
                root_fact_sha256=action.root_fact_sha256,
                is_baseline=action.is_baseline,
                computation=reference_computation,
                worker_seconds=float(reference.worker_seconds),
            )
        )
    audit = finalize_gate3_audit(
        sample,
        tuple(generator_records),
        tuple(checker_records),
        tuple(v2_records),
        tuple(reference_records),
    )
    return audit, tuple(reference_timings)


def build_gate3_root_membership_attestation(
    portable_fact_gate3: M8PortableFactGate3Result,
    checked_action_roots: tuple[M8Gate3CheckedActionRoot, ...],
    *,
    producer_id: str,
    producer_content_sha256: str,
    runtime_id: str,
    runtime_content_sha256: str,
) -> M8Gate3RootMembershipAttestation:
    """Commit an executor-checked 887-root sequence to the Task-7 cells."""

    gate3 = require_official_portable_gate3(
        portable_fact_gate3,
        label="M8 Gate-3 root-membership attestation",
    )
    roots = tuple(
        M8Gate3CheckedActionRoot.model_validate_json(item.model_dump_json(), strict=True)
        for item in checked_action_roots
    )
    if len(roots) != 887:
        raise ValueError("M8 Gate-3 membership requires exactly 887 checked roots")
    bindings = tuple(
        M8Gate3RootMembershipBinding(
            regime=cell.regime,
            source_bundle_sha256=cell.first_bundle_sha256,
            source_semantic_bundle_bytes_sha256=(
                cell.first_semantic_bundle_bytes_sha256
            ),
            checker_decision_id=cell.decision_id,
            checker_decision_content_sha256=cell.decision_content_sha256,
            checked_root_count=cell.checked_action_root_count,
        )
        for cell in gate3.cells
    )
    semantic = {
        "schema_version": "yieldforge.m8-gate3-root-membership-attestation.v1",
        "portable_gate3_id": gate3.gate3_id,
        "portable_gate3_content_sha256": gate3.content_sha256,
        "producer_id": producer_id,
        "producer_content_sha256": producer_content_sha256,
        "runtime_id": runtime_id,
        "runtime_content_sha256": runtime_content_sha256,
        "bindings": tuple(item.model_dump(mode="json") for item in bindings),
        "checked_root_count": 887,
        "checked_root_sequence_sha256": gate3_checked_root_sequence_sha256(roots),
        "source_verification_scope": (
            "external_strict_canonical_bundle_and_checked_result_membership"
        ),
        "canonical_bundle_bytes_retained_by_executor": True,
        "checked_result_retained_by_executor": True,
        "producer_exit_code": 0,
        "surviving_descendant_count": 0,
        "surviving_registry_count": 0,
        "evaluation_accessed": False,
        "claim_ceiling": (
            "executor_attested_checked_bundle_membership_only_source_bytes_not_embedded_or_"
            "reverified_here"
        ),
    }
    digest = semantic_sha256(semantic)
    return M8Gate3RootMembershipAttestation(
        attestation_id=f"yfm8g3membership-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        portable_gate3_id=gate3.gate3_id,
        portable_gate3_content_sha256=gate3.content_sha256,
        producer_id=producer_id,
        producer_content_sha256=producer_content_sha256,
        runtime_id=runtime_id,
        runtime_content_sha256=runtime_content_sha256,
        bindings=bindings,  # type: ignore[arg-type]
        checked_root_sequence_sha256=gate3_checked_root_sequence_sha256(roots),
    )


def execute_gate3_decision(
    *,
    index: M7CalibrationProblemView,
    m0: M0ExperimentContract,
    frozen: M7FrozenBaseline,
    parent_v3: M8CertificateProofResult,
    portable_fact_gate3: M8PortableFactGate3Result,
    archive_roots: tuple[Path, ...],
    jagua_executable: Path,
    progress=None,  # type: ignore[no-untyped-def]
) -> M8Gate3Decision:
    """Execute the complete sealed audit, mutation, and performance decision."""

    if (
        type(index) is not M7CalibrationProblemView
        or type(m0) is not M0ExperimentContract
        or type(frozen) is not M7FrozenBaseline
        or type(parent_v3) is not M8CertificateProofResult
        or type(portable_fact_gate3) is not M8PortableFactGate3Result
    ):
        raise TypeError("M8 Gate-3 execution requires exact frozen input contracts")
    source_tree = _capture_yieldforge_source_tree()
    from yieldforge.oracle import gate3_mutations as mutations_module

    parent = M8CertificateProofResult.model_validate_json(
        parent_v3.model_dump_json(), strict=True
    )
    gate3 = require_official_portable_gate3(
        portable_fact_gate3,
        label="M8 Gate-3 execution",
    )
    if (
        parent.proof_id != gate3.parent_v3_proof_id
        or parent.content_sha256 != gate3.parent_v3_content_sha256
        or parent.evaluation_partition_opened
        or gate3.evaluation_accessed
    ):
        raise ValueError("M8 Gate-3 parent or portable evidence differs from the freeze")
    cells, rules, executable, executable_sha = _prepare_gate3_execution_cells(
        index=index,
        m0=m0,
        frozen=frozen,
        portable_fact_gate3=gate3,
        archive_roots=archive_roots,
        jagua_executable=jagua_executable,
    )

    if progress is not None:
        progress("phase_start phase=gate3_checked_source_capture probes=2")
    with _activate_gate3_source_attested_spawns(
        source_tree,
        mutations_module=mutations_module,
    ):
        retained_sources = experiment_module._capture_portable_fact_checked_sources(
            cells,
            rules=rules,
            jagua_executable=executable,
            freeze_id=frozen.freeze_id,
            freeze_sha256=frozen.content_sha256,
            expected_jagua_sha256=executable_sha,
            budget=M8_GATE3_CONCURRENCY_BUDGET,
        )
    corpus = _extract_gate3_checked_corpus(gate3, retained_sources)
    if progress is not None:
        progress("phase_complete phase=gate3_checked_source_capture roots=887")

    runtime = _runtime_identity(jagua_executable_sha256=executable_sha)
    membership_implementation = _implementation_identity(
        "root-membership",
        (
            Path(__file__),
            _module_source(experiment_module),
            _module_source(fact_checker_module),
            _module_source(gate3_evidence_module),
        ),
        source_tree=source_tree,
    )
    membership = build_gate3_root_membership_attestation(
        gate3,
        corpus.roots,
        producer_id=membership_implementation[0],
        producer_content_sha256=membership_implementation[1],
        runtime_id=runtime[0],
        runtime_content_sha256=runtime[1],
    )
    manifest = freeze_gate3_checked_root_manifest(
        gate3,
        corpus.roots,
        membership_attestation=membership,
    )
    sample = freeze_gate3_audit_sample(parent, manifest)
    if progress is not None:
        progress(
            "phase_complete phase=gate3_sample_freeze "
            f"manifest={manifest.manifest_id} sample={sample.sample_id}"
        )

    with _activate_gate3_source_attested_spawns(
        source_tree,
        mutations_module=mutations_module,
    ):
        audit, reference_timings = _run_gate3_four_way_audit(
            sample=sample,
            corpus=corpus,
            execution_cells=cells,
            rules=rules,
            jagua_executable=executable,
            freeze_id=frozen.freeze_id,
            freeze_sha256=frozen.content_sha256,
            jagua_executable_sha256=executable_sha,
            source_tree=source_tree,
            progress=progress,
        )
    if progress is not None:
        progress(
            "phase_complete phase=gate3_four_way_audit "
            f"decision={audit.proof_decision} mismatches={audit.total_mismatch_count}"
        )

    mutation_implementation = _implementation_identity(
        "mutation-harness",
        (
            Path(__file__),
            _module_source(mutations_module),
            _module_source(experiment_module),
            _module_source(fact_checker_module),
            _module_source(facts_module),
            _module_source(gate3_evidence_module),
        ),
        source_tree=source_tree,
    )
    if progress is not None:
        progress("phase_start phase=gate3_mutations targets=16")
    with _activate_gate3_source_attested_spawns(
        source_tree,
        mutations_module=mutations_module,
    ):
        mutations = mutations_module.execute_gate3_mutations(
            parent,
            gate3,
            manifest,
            sample,
            tuple(item.semantic_bundle_bytes for item in corpus.sources),
            checker_contexts=tuple(
                mutations_module.M8Gate3MutationCheckerContext(
                    execution_cell=cell,
                    rules=rules,
                    jagua_executable=executable,
                    freeze_id=frozen.freeze_id,
                    freeze_sha256=frozen.content_sha256,
                    expected_jagua_sha256=executable_sha,
                    translation_audit_processes=(
                        M8_GATE3_CONCURRENCY_BUDGET.translation_audit_processes_per_cell
                    ),
                )
                for cell in cells
            ),
            harness_id=mutation_implementation[0],
            harness_content_sha256=mutation_implementation[1],
            runtime_id=runtime[0],
            runtime_content_sha256=runtime[1],
            timeout_seconds=1800.0,
        )
    if progress is not None:
        progress(
            "phase_complete phase=gate3_mutations "
            f"decision={mutations.mutation_decision} "
            f"rejected={mutations.rejected_mutation_count}/16"
        )

    performance = finalize_gate3_performance(
        gate3,
        manifest,
        sample,
        reference_timings=reference_timings,
    )
    decision = finalize_gate3_decision(
        parent,
        gate3,
        manifest,
        sample,
        audit,
        mutations,
        performance,
    )
    strict = M8Gate3Decision.model_validate_json(
        decision.model_dump_json(),
        strict=True,
    )
    if progress is not None:
        progress(
            "phase_complete phase=gate3_decision "
            f"decision={strict.decision} "
            f"speedup={performance.reference_equivalent_speedup} "
            f"projected_days={performance.projected_held_out_calendar_days}"
        )
    _require_yieldforge_source_tree_unchanged(source_tree)
    return strict


def publish_gate3_decision(
    output_directory: Path,
    decision: M8Gate3Decision,
) -> Path:
    """Atomically publish one immutable, content-addressed complete decision."""

    if type(decision) is not M8Gate3Decision:
        raise TypeError("M8 Gate-3 publisher requires the exact decision model")
    strict = M8Gate3Decision.model_validate_json(decision.model_dump_json(), strict=True)
    output = Path(output_directory)
    if output.exists() and not output.is_dir():
        raise ValueError("M8 Gate-3 output must be a directory")
    path = output / f"m8-gate3-decision-{strict.decision_id}.json"
    data = _canonical_gate3_decision_artifact(strict)
    try:
        return publish_immutable_artifact(
            path,
            data,
            validate=_validate_gate3_decision_artifact,
            label="M8 Gate-3 decision artifact",
        )
    except M8ArtifactPublicationError as error:
        if error.kind in {"conflict", "destination"}:
            raise M8ArtifactPublicationError(
                "M8 Gate-3 decision artifact",
                error.kind,
                "is immutable and differs",
            ) from error
        cause = error.__cause__
        if error.kind in {"write", "fsync", "install"} and isinstance(cause, OSError):
            raise cause from error
        raise


def _canonical_gate3_decision_artifact(decision: M8Gate3Decision) -> bytes:
    return (
        json.dumps(
            decision.model_dump(mode="json"),
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()


def _validate_gate3_decision_artifact(data: bytes) -> bytes:
    strict = M8Gate3Decision.model_validate_json(data, strict=True)
    return _canonical_gate3_decision_artifact(strict)


__all__ = [
    "build_gate3_root_membership_attestation",
    "execute_gate3_decision",
    "load_portable_fact_gate3",
    "publish_gate3_decision",
]
