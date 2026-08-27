from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.oracle.test_gate3_evidence import (
    _audit_inputs,
    _checked_roots,
    _freeze_manifest,
    _mutation_evidence,
    _portable_gate3_result,
    _reference_timings,
)
from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.oracle import gate3_execution as gate3_execution_module
from yieldforge.oracle import reference as reference_module
from yieldforge.oracle.gate3_evidence import (
    finalize_gate3_audit,
    finalize_gate3_decision,
    finalize_gate3_performance,
    freeze_gate3_audit_sample,
    load_parent_v3_certificate_proof,
)
from yieldforge.oracle.gate3_execution import (
    _Gate3CheckedCorpus,
    _implementation_identity,
    _runtime_identity,
    _sample_action_shards,
    build_gate3_root_membership_attestation,
    execute_gate3_decision,
    load_portable_fact_gate3,
    publish_gate3_decision,
)

_RESULTS = Path(__file__).resolve().parents[2] / "experiments/results"
_PARENT = _RESULTS / (
    "m8-certificate-proof-yfm8proof-b296ba919c07d55ece14c6db.json"
)
_PORTABLE = _RESULTS / (
    "m8-portable-fact-gate3-yfm8gate3-ea8a12969396172d7dbc4774.json"
)


def _sha(digit: str) -> str:
    return "sha256:" + digit * 64


def _pycache_probe(value: str) -> tuple[str, str | None, str | None]:
    return value, os.environ.get("PYTHONPYCACHEPREFIX"), sys.pycache_prefix


def _write_source_tree(root: Path) -> tuple[Path, Path, Path]:
    package = root / "yieldforge"
    oracle = package / "oracle"
    oracle.mkdir(parents=True)
    primary = oracle / "primary.py"
    secondary = oracle / "secondary.py"
    (package / "__init__.py").write_text('"""Test package."""\n')
    (package / "shared.py").write_text("VALUE = 1\n")
    primary.write_text("from yieldforge.shared import VALUE\n")
    secondary.write_text("from yieldforge.shared import VALUE\n")
    (package / "generated.json").write_text('{"mutable": true}\n')
    return package, primary, secondary


def _complete_decision():  # type: ignore[no-untyped-def]
    parent = load_parent_v3_certificate_proof(_PARENT)
    gate3 = load_portable_fact_gate3(_PORTABLE)
    roots = _checked_roots(gate3)
    membership = build_gate3_root_membership_attestation(
        gate3,
        roots,
        producer_id="m8-gate3-test-extractor-v1",
        producer_content_sha256=_sha("a"),
        runtime_id="m8-gate3-test-runtime-v1",
        runtime_content_sha256=_sha("b"),
    )
    manifest = _freeze_manifest(gate3, roots).model_copy(
        update={"membership_attestation": membership}
    )
    # Re-freeze so the outer manifest identity commits to the production attestation.
    from yieldforge.oracle.gate3_evidence import freeze_gate3_checked_root_manifest

    manifest = freeze_gate3_checked_root_manifest(
        gate3,
        roots,
        membership_attestation=membership,
    )
    sample = freeze_gate3_audit_sample(parent, manifest)
    audit = finalize_gate3_audit(sample, *_audit_inputs(sample))
    _, _, mutations = _mutation_evidence(parent, gate3, manifest, sample)
    performance = finalize_gate3_performance(
        gate3,
        manifest,
        sample,
        reference_timings=_reference_timings(sample),
    )
    return finalize_gate3_decision(
        parent,
        gate3,
        manifest,
        sample,
        audit,
        mutations,
        performance,
    )


def test_load_portable_gate3_strictly_rejects_tampering(tmp_path: Path) -> None:
    loaded = load_portable_fact_gate3(_PORTABLE)
    assert loaded.gate3_id == "yfm8gate3-ea8a12969396172d7dbc4774"

    raw = json.loads(_PORTABLE.read_text())
    raw["total_pipeline_wall_seconds"] += 1.0
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="portable Gate-3"):
        load_portable_fact_gate3(tampered)

    draft = loaded.model_copy(
        update={
            "gate3_id": "yfm8gate3-" + "0" * 24,
            "content_sha256": "sha256:" + "0" * 64,
            "total_pipeline_wall_seconds": loaded.total_pipeline_wall_seconds + 1.0,
        }
    )
    digest = semantic_sha256(draft, excluded_fields={"gate3_id", "content_sha256"})
    forged = draft.model_copy(
        update={
            "gate3_id": f"yfm8gate3-{digest[:24]}",
            "content_sha256": f"sha256:{digest}",
        }
    )
    forged = type(loaded).model_validate_json(forged.model_dump_json(), strict=True)
    coherently_rehashed = tmp_path / "coherently-rehashed.json"
    coherently_rehashed.write_text(forged.model_dump_json())
    with pytest.raises(ValueError, match="failed strict validation"):
        load_portable_fact_gate3(coherently_rehashed)


def test_membership_attestation_is_content_addressed_and_bound_to_all_roots() -> None:
    gate3 = load_portable_fact_gate3(_PORTABLE)
    roots = _checked_roots(gate3)
    attestation = build_gate3_root_membership_attestation(
        gate3,
        roots,
        producer_id="m8-gate3-test-extractor-v1",
        producer_content_sha256=_sha("a"),
        runtime_id="m8-gate3-test-runtime-v1",
        runtime_content_sha256=_sha("b"),
    )

    assert attestation.checked_root_count == 887
    assert tuple(item.checked_root_count for item in attestation.bindings) == (428, 459)
    assert type(attestation).model_validate_json(
        attestation.model_dump_json(), strict=True
    ) == attestation

    with pytest.raises(ValueError, match="887"):
        build_gate3_root_membership_attestation(
            gate3,
            roots[:-1],
            producer_id="m8-gate3-test-extractor-v1",
            producer_content_sha256=_sha("a"),
            runtime_id="m8-gate3-test-runtime-v1",
            runtime_content_sha256=_sha("b"),
        )


def test_source_tree_identity_is_relative_relocation_stable_and_python_only(
    tmp_path: Path,
) -> None:
    first_package, first_primary, _ = _write_source_tree(tmp_path / "first")
    second_package, second_primary, _ = _write_source_tree(tmp_path / "second")
    first = gate3_execution_module._capture_yieldforge_source_tree(first_package)
    second = gate3_execution_module._capture_yieldforge_source_tree(second_package)

    assert tuple(item.relative_path for item in first.source_files) == (
        "yieldforge/__init__.py",
        "yieldforge/oracle/primary.py",
        "yieldforge/oracle/secondary.py",
        "yieldforge/shared.py",
    )
    assert all(not Path(item.relative_path).is_absolute() for item in first.source_files)
    assert _implementation_identity(
        "v1-generator",
        (first_primary,),
        source_tree=first,
    ) == _implementation_identity(
        "v1-generator",
        (second_primary,),
        source_tree=second,
    )

    (second_package / "generated.json").write_text('{"mutable": false}\n')
    unchanged = gate3_execution_module._capture_yieldforge_source_tree(second_package)
    assert _implementation_identity(
        "v1-generator",
        (second_primary,),
        source_tree=unchanged,
    ) == _implementation_identity(
        "v1-generator",
        (first_primary,),
        source_tree=first,
    )


def test_source_tree_identity_commits_to_transitive_sources_without_role_salts(
    tmp_path: Path,
) -> None:
    package, primary, secondary = _write_source_tree(tmp_path)
    initial = gate3_execution_module._capture_yieldforge_source_tree(package)
    generator = _implementation_identity(
        "v1-generator",
        (primary,),
        source_tree=initial,
    )
    checker = _implementation_identity(
        "v1-checker",
        (primary,),
        source_tree=initial,
    )
    distinct_primary = _implementation_identity(
        "reference",
        (secondary,),
        source_tree=initial,
    )
    assert generator[1] == checker[1]
    assert generator[1] != distinct_primary[1]

    (package / "shared.py").write_text("VALUE = 2\n")
    changed = gate3_execution_module._capture_yieldforge_source_tree(package)
    assert _implementation_identity(
        "v1-generator",
        (primary,),
        source_tree=changed,
    )[1] != generator[1]


def test_source_tree_identity_rejects_out_of_package_and_symlink_sources(
    tmp_path: Path,
) -> None:
    package, primary, _ = _write_source_tree(tmp_path / "tree")
    snapshot = gate3_execution_module._capture_yieldforge_source_tree(package)
    outside = tmp_path / "outside.py"
    outside.write_text("VALUE = 1\n")
    with pytest.raises(ValueError, match="package source tree"):
        _implementation_identity(
            "reference",
            (outside,),
            source_tree=snapshot,
        )

    link = package / "oracle" / "linked.py"
    link.symlink_to(primary)
    with pytest.raises(ValueError, match="symlink"):
        gate3_execution_module._capture_yieldforge_source_tree(package)


def test_source_tree_stability_check_rejects_mid_execution_changes(tmp_path: Path) -> None:
    package, _, _ = _write_source_tree(tmp_path)
    snapshot = gate3_execution_module._capture_yieldforge_source_tree(package)
    (package / "shared.py").write_text("VALUE = 2\n")

    with pytest.raises(ValueError, match="changed during execution"):
        gate3_execution_module._require_yieldforge_source_tree_unchanged(snapshot)


def test_attested_process_runner_checks_worker_entry_and_isolates_pycache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import experiment as experiment_module
    from yieldforge.oracle import gate3_mutations as mutations_module

    package, _, _ = _write_source_tree(tmp_path / "source")
    snapshot = gate3_execution_module._capture_yieldforge_source_tree(package)
    original_experiment_runner = experiment_module._run_process_phase
    original_mutation_runner = mutations_module._run_process_phase
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", "preexisting-prefix")

    with gate3_execution_module._activate_gate3_source_attested_spawns(
        snapshot,
        mutations_module=mutations_module,
    ) as pycache_prefix:
        assert pycache_prefix.is_dir()
        assert experiment_module._run_process_phase is mutations_module._run_process_phase
        result = experiment_module._run_process_phase(
            _pycache_probe,
            (("verified",),),
            process_count=1,
        )
        assert result == (
            (
                "verified",
                str(pycache_prefix),
                str(pycache_prefix),
            ),
        )

        (package / "shared.py").write_text("VALUE = 2\n")
        with pytest.raises(RuntimeError, match="source tree changed during execution"):
            mutations_module._run_process_phase(
                _pycache_probe,
                (("rejected",),),
                process_count=1,
            )

    assert experiment_module._run_process_phase is original_experiment_runner
    assert mutations_module._run_process_phase is original_mutation_runner
    assert os.environ["PYTHONPYCACHEPREFIX"] == "preexisting-prefix"


def test_audit_shards_and_source_identities_are_frozen_without_role_salts() -> None:
    parent = load_parent_v3_certificate_proof(_PARENT)
    gate3 = _portable_gate3_result()
    roots = _checked_roots(gate3)
    manifest = _freeze_manifest(gate3, roots)
    sample = freeze_gate3_audit_sample(parent, manifest)

    shards = _sample_action_shards(sample)
    assert tuple(len(items) for _, items in shards) == (6, 6)
    assert tuple(regime.value for regime, _ in shards) == (
        "no_signal",
        "regime_shift",
    )

    source_tree = gate3_execution_module._capture_yieldforge_source_tree()
    source = Path(gate3_execution_module.__file__)
    reference_source = Path(reference_module.__file__)
    generator = _implementation_identity(
        "v1-generator", (source,), source_tree=source_tree
    )
    checker = _implementation_identity(
        "v1-checker", (source,), source_tree=source_tree
    )
    reference = _implementation_identity(
        "reference", (reference_source,), source_tree=source_tree
    )
    assert generator[1] == checker[1]
    assert generator[1] != reference[1]
    assert _runtime_identity(jagua_executable_sha256=_sha("c")) == (
        "yieldforge-m8-gate3-runtime-v1",
        _runtime_identity(jagua_executable_sha256=_sha("c"))[1],
    )


def test_gate3_decision_publisher_is_atomic_idempotent_and_immutable(
    tmp_path: Path,
) -> None:
    decision = _complete_decision()
    path = publish_gate3_decision(tmp_path, decision)

    assert path.name == f"m8-gate3-decision-{decision.decision_id}.json"
    assert publish_gate3_decision(tmp_path, decision) == path
    assert type(decision).model_validate_json(path.read_bytes(), strict=True) == decision

    path.write_text("{}\n")
    with pytest.raises(ValueError, match="immutable"):
        publish_gate3_decision(tmp_path, decision)


def test_complete_executor_binds_all_phases_without_opening_evaluation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tests.baseline.test_replay import _m0
    from yieldforge.baseline.experiment import M7FrozenBaseline
    from yieldforge.baseline.problems import (
        build_registered_calibration_problem_view,
        build_registered_problem_index,
    )
    from yieldforge.oracle import gate3_execution, gate3_mutations

    parent = load_parent_v3_certificate_proof(_PARENT)
    gate3 = load_portable_fact_gate3(_PORTABLE)
    roots = _checked_roots(gate3)
    full_index = build_registered_problem_index()
    index = build_registered_calibration_problem_view(
        full_problem_index_id=full_index.index_id,
        full_problem_index_sha256=full_index.content_sha256,
    )
    m0 = _m0()
    frozen = M7FrozenBaseline.model_validate_json(
        (_RESULTS / "m7-frozen-baseline-v1.0.1.json").read_bytes(),
        strict=True,
    )
    sources = (
        SimpleNamespace(semantic_bundle_bytes=b"first"),
        SimpleNamespace(semantic_bundle_bytes=b"second"),
    )
    corpus = _Gate3CheckedCorpus(
        roots=roots,
        bundles=(object(), object()),  # type: ignore[arg-type]
        sources=sources,  # type: ignore[arg-type]
    )
    phases: list[str] = []
    source_tree = gate3_execution._capture_yieldforge_source_tree()
    source_tree_capture_count = 0

    def capture_source_tree(package_root=None):  # type: ignore[no-untyped-def]
        nonlocal source_tree_capture_count
        source_tree_capture_count += 1
        assert package_root is None or Path(package_root) == source_tree.package_root
        if package_root is not None:
            assert any("phase_complete phase=gate3_decision" in item for item in phases)
        return source_tree

    monkeypatch.setattr(
        gate3_execution,
        "_capture_yieldforge_source_tree",
        capture_source_tree,
    )

    monkeypatch.setattr(
        gate3_execution,
        "_prepare_gate3_execution_cells",
        lambda **kwargs: (
            (object(), object()),
            object(),
            tmp_path / "jagua",
            _sha("c"),
        ),
    )
    monkeypatch.setattr(
        gate3_execution.experiment_module,
        "_capture_portable_fact_checked_sources",
        lambda *args, **kwargs: sources,
    )
    monkeypatch.setattr(
        gate3_execution,
        "_extract_gate3_checked_corpus",
        lambda *args, **kwargs: corpus,
    )

    def audit_phase(**kwargs):  # type: ignore[no-untyped-def]
        sample = kwargs["sample"]
        audit = finalize_gate3_audit(sample, *_audit_inputs(sample))
        return audit, _reference_timings(sample)

    monkeypatch.setattr(gate3_execution, "_run_gate3_four_way_audit", audit_phase)

    def mutations_phase(parent_arg, gate3_arg, manifest, sample, bundles, **kwargs):  # type: ignore[no-untyped-def]
        assert parent_arg == parent
        assert gate3_arg == gate3
        assert bundles == (b"first", b"second")
        assert len(kwargs["checker_contexts"]) == 2
        return _mutation_evidence(parent, gate3, manifest, sample)[2]

    monkeypatch.setattr(
        gate3_mutations,
        "M8Gate3MutationCheckerContext",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(gate3_mutations, "execute_gate3_mutations", mutations_phase)

    result = execute_gate3_decision(
        index=index,
        m0=m0,
        frozen=frozen,
        parent_v3=parent,
        portable_fact_gate3=gate3,
        archive_roots=(tmp_path,),
        jagua_executable=tmp_path / "jagua",
        progress=phases.append,
    )

    assert result.decision == "hold_performance"
    assert result.evaluation_opened is False
    assert result.official_six_cell_executed is False
    assert source_tree_capture_count == 2
    assert any("gate3_checked_source_capture" in item for item in phases)
    assert any("gate3_four_way_audit" in item for item in phases)
    assert any("gate3_mutations" in item for item in phases)
    assert any("gate3_decision" in item for item in phases)
