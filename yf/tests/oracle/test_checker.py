from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from tests.oracle.fixtures import two_problem_runtime
from yieldforge.baseline.replay import initial_m7_cursor
from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.oracle.proofs import M8ActionProof, build_m8_action_proof
from yieldforge.oracle.reference import M8OracleRequest
from yieldforge.oracle.sparse import score_sparse_event
from yieldforge.oracle.visibility import FullRealizedVisibility


def _request(*, passive: bool = True) -> M8OracleRequest:
    runtime = two_problem_runtime(
        first_width=9.0 if passive else 4.0,
        second_width=4.0,
    )
    return M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )


def _rebuild(proof: M8ActionProof, **changes: object) -> M8ActionProof:
    fields = {
        "action_id": proof.action_id,
        "catalog_action_id": proof.catalog_action_id,
        "baseline_action_id": proof.baseline_action_id,
        "baseline_catalog_action_id": proof.baseline_catalog_action_id,
        "start_event_position": proof.start_event_position,
        "stop_event_position": proof.stop_event_position,
        "suffix_sha256": proof.suffix_sha256,
        "semantic_runtime_sha256": proof.semantic_runtime_sha256,
        "start_state_sha256": proof.start_state_sha256,
        "witnesses": proof.witnesses,
        "final_net_cost": proof.final_net_cost,
        "final_state_sha256": proof.final_state_sha256,
    }
    fields.update(changes)
    return build_m8_action_proof(**fields)  # type: ignore[arg-type]


def _proof_with_classification(
    request: M8OracleRequest,
    classification: str,
) -> M8ActionProof:
    return next(
        proof
        for proof in score_sparse_event(request).proofs
        if any(witness.classification == classification for witness in proof.witnesses)
    )


def _unsafe_rehash(proof: M8ActionProof, **changes: object) -> M8ActionProof:
    provisional = proof.model_copy(update=changes)
    digest = semantic_sha256(
        provisional,
        excluded_fields={"proof_id", "content_sha256"},
    )
    return provisional.model_copy(
        update={
            "proof_id": f"yfm8ap-{digest[:24]}",
            "content_sha256": f"sha256:{digest}",
        }
    )


@pytest.mark.parametrize("source_classification", ["state_rejoin", "no_fit"])
def test_checker_rejects_rehashed_noncanonical_exact_transition(
    source_classification: str,
) -> None:
    from yieldforge.oracle.checker import check_action_proof

    request = _request()
    proof = _proof_with_classification(request, source_classification)
    source = next(
        witness
        for witness in proof.witnesses
        if witness.classification == source_classification
    )
    replacement = source.model_copy(
        update={
            "classification": "exact_transition",
            "influences": (),
        }
    )
    tampered = _rebuild(proof, witnesses=(replacement,))

    result = check_action_proof(request, tampered)

    assert not result.valid
    assert result.failure_code == "witness_mismatch"


def test_checker_is_independent_of_both_scoring_control_flows(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from yieldforge.oracle import reference, sparse
    from yieldforge.oracle.checker import check_action_proof

    request = _request()
    proof = score_sparse_event(request).proofs[0]
    monkeypatch.setattr(
        sparse,
        "score_certificate_action",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("called generator")),
    )
    monkeypatch.setattr(
        reference,
        "score_reference_action",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("called reference")),
    )

    result = check_action_proof(request, proof)

    assert result.valid
    assert result.failure_code == "valid"
    assert result.checked_event_count == len(proof.witnesses)


@pytest.mark.parametrize(
    "mutation",
    [
        "event_position",
        "state_hash",
        "decision_key",
        "score",
        "suffix_hash",
        "runtime_binding",
        "start_state",
        "catalog_action_id",
        "materialized_action_id",
    ],
)
def test_checker_fails_closed_on_independently_rehashed_tampering(
    mutation: str,
) -> None:
    from yieldforge.oracle.checker import check_action_proof

    request = _request()
    proof = _proof_with_classification(request, "no_fit")
    if mutation == "event_position":
        witness = proof.witnesses[0].model_copy(
            update={"event_position": proof.witnesses[0].event_position + 1}
        )
        tampered = _unsafe_rehash(proof, witnesses=(witness,))
    elif mutation == "state_hash":
        witness = proof.witnesses[0].model_copy(
            update={"state_after_sha256": "sha256:" + "f" * 64}
        )
        tampered = _rebuild(proof, witnesses=(witness,))
    elif mutation == "decision_key":
        witness = proof.witnesses[0]
        influence = witness.influences[0].model_copy(
            update={
                "common_decision_key": (
                    *witness.influences[0].common_decision_key,
                    "tampered=true",
                )
            }
        )
        tampered = _rebuild(
            proof,
            witnesses=(witness.model_copy(update={"influences": (influence,)}),),
        )
    elif mutation == "score":
        tampered = _rebuild(proof, final_net_cost=proof.final_net_cost + 1.0)
    elif mutation == "suffix_hash":
        tampered = _rebuild(proof, suffix_sha256="sha256:" + "f" * 64)
    elif mutation == "runtime_binding":
        tampered = _rebuild(proof, semantic_runtime_sha256="sha256:" + "f" * 64)
    elif mutation == "start_state":
        tampered = _rebuild(proof, start_state_sha256="sha256:" + "f" * 64)
    elif mutation == "catalog_action_id":
        tampered = _rebuild(proof, catalog_action_id="m7-standard:missing")
    elif mutation == "materialized_action_id":
        tampered = _rebuild(proof, action_id="yfm7a-" + "f" * 24)
    else:  # pragma: no cover
        raise AssertionError(mutation)

    result = check_action_proof(request, tampered)

    assert not result.valid
    assert result.failure_code != "valid"


def test_checker_validates_exact_transition_and_final_state() -> None:
    from yieldforge.oracle.checker import check_action_proof

    request = _request(passive=False)
    proof = _proof_with_classification(request, "exact_transition")
    result = check_action_proof(request, proof)

    assert result.valid
    assert result.exact_transition_count > 0
    assert result.checked_event_count == len(proof.witnesses)

    tampered = _rebuild(proof, final_state_sha256="sha256:" + "f" * 64)
    assert not check_action_proof(request, tampered).valid


def test_batch_checker_matches_standalone_results() -> None:
    from yieldforge.oracle.checker import check_action_proof, check_action_proofs

    request = _request()
    proofs = score_sparse_event(request).proofs

    assert check_action_proofs(request, proofs) == tuple(
        check_action_proof(request, proof) for proof in proofs
    )


def test_generator_and_checker_are_separate_scoped_authorities() -> None:
    from yieldforge.oracle.checker import _prepare_m8_checker_context
    from yieldforge.oracle.sparse import _prepare_m8_generator_context

    request = _request()
    with _prepare_m8_generator_context(request) as generator:
        generator_authority = generator._authority  # noqa: SLF001
        with _prepare_m8_checker_context(request) as checker:
            checker_authority = checker._authority  # noqa: SLF001
            assert generator_authority is not checker_authority
            assert generator_authority.runtime is not checker_authority.runtime
        with pytest.raises(ValueError, match="no longer active"):
            checker_authority.require_active()
        generator_authority.require_active()
    with pytest.raises(ValueError, match="no longer active"):
        generator_authority.require_active()


def test_prepared_apis_reject_crossed_reconstructed_and_copied_contexts() -> None:
    from yieldforge.oracle.checker import (
        _check_prepared_action_proofs,
        _prepare_m8_checker_context,
    )
    from yieldforge.oracle.sparse import (
        _prepare_m8_generator_context,
        _score_prepared_certificate_actions,
    )

    request = _request()
    proofs = score_sparse_event(request).proofs
    with _prepare_m8_generator_context(request) as generator:
        with _prepare_m8_checker_context(request) as checker:
            with pytest.raises(ValueError, match="prepared checker capability"):
                _check_prepared_action_proofs(generator, proofs)  # type: ignore[arg-type]
            with pytest.raises(ValueError, match="prepared generator capability"):
                _score_prepared_certificate_actions(checker)  # type: ignore[arg-type]

            reconstructed_generator = replace(generator)
            reconstructed_checker = replace(checker)
            with pytest.raises(ValueError, match="prepared generator capability"):
                _score_prepared_certificate_actions(reconstructed_generator)
            with pytest.raises(ValueError, match="prepared checker capability"):
                _check_prepared_action_proofs(reconstructed_checker, proofs)

            with pytest.raises(TypeError, match="cannot be serialized"):
                copy.copy(generator)
            with pytest.raises(TypeError, match="cannot be serialized"):
                copy.copy(checker)


def test_checker_context_mutation_cannot_authorize_rehashed_suffix() -> None:
    from yieldforge.oracle.checker import (
        _check_prepared_action_proofs,
        _prepare_m8_checker_context,
    )

    request = _request()
    proof = score_sparse_event(request).proofs[0]
    forged_suffix = "sha256:" + "f" * 64
    forged = _rebuild(proof, suffix_sha256=forged_suffix)
    with _prepare_m8_checker_context(request) as context:
        original = context._suffix_sha256  # noqa: SLF001
        object.__setattr__(context, "_suffix_sha256", forged_suffix)
        try:
            with pytest.raises(ValueError, match="prepared checker capability"):
                _check_prepared_action_proofs(context, (forged,))
        finally:
            object.__setattr__(context, "_suffix_sha256", original)


def test_generator_context_fingerprint_rejects_low_level_mutation() -> None:
    from yieldforge.oracle.sparse import (
        _prepare_m8_generator_context,
        _score_prepared_certificate_actions,
    )

    request = _request()
    with _prepare_m8_generator_context(request) as context:
        original = context._fallback_step  # noqa: SLF001
        altered = replace(
            original,
            cursor=replace(
                original.cursor,
                timestamp_subsequence=original.cursor.timestamp_subsequence + 1,
            ),
        )
        object.__setattr__(context, "_fallback_step", altered)
        try:
            with pytest.raises(ValueError, match="prepared generator capability"):
                _score_prepared_certificate_actions(context)
        finally:
            object.__setattr__(context, "_fallback_step", original)


@pytest.mark.parametrize("mutation", ["stop", "catalog", "request"])
def test_prepared_checker_fingerprint_detects_low_level_field_mutation(
    mutation: str,
) -> None:
    from yieldforge.oracle.checker import _prepare_m8_checker_context

    request = _request()
    with _prepare_m8_checker_context(request) as context:
        if mutation == "stop":
            field_name = "_stop_event_position"
            original = context._stop_event_position  # noqa: SLF001
            altered = original + 1
        elif mutation == "catalog":
            field_name = "_catalog"
            original = context._catalog  # noqa: SLF001
            altered = replace(original, event_position=original.event_position + 1)
        elif mutation == "request":
            field_name = "_request"
            original = context._request  # noqa: SLF001
            altered = replace(
                original,
                cursor=replace(
                    original.cursor,
                    timestamp_subsequence=original.cursor.timestamp_subsequence + 1,
                ),
            )
        else:  # pragma: no cover - parametrization is exhaustive.
            raise AssertionError(mutation)
        object.__setattr__(context, field_name, altered)
        try:
            with pytest.raises(ValueError, match="prepared checker capability"):
                context.require_active()
        finally:
            object.__setattr__(context, field_name, original)


@pytest.mark.parametrize("mutation", ["semantic_hash", "runtime_rules"])
def test_authoritative_runtime_fingerprint_detects_low_level_mutation(
    mutation: str,
) -> None:
    from yieldforge.baseline.replay import authoritative_m7_proof_runtime

    request = _request()
    with authoritative_m7_proof_runtime(request.runtime) as authority:
        if mutation == "semantic_hash":
            target = authority
            field_name = "semantic_sha256"
            original = authority.semantic_sha256
            altered = "sha256:" + "f" * 64
        else:
            target = authority.runtime
            field_name = "rules"
            original = authority.runtime.rules
            altered = original.model_copy(
                update={
                    "rule_set_id": "yfrules-" + "f" * 24,
                    "content_sha256": "sha256:" + "f" * 64,
                }
            )
        object.__setattr__(target, field_name, altered)
        try:
            with pytest.raises(ValueError, match="authoritative proof runtime"):
                authority.require_active()
        finally:
            object.__setattr__(target, field_name, original)


def test_generator_authority_cleans_up_when_scoring_raises() -> None:
    from yieldforge.oracle.sparse import _prepare_m8_generator_context

    request = _request()
    authority = None
    with pytest.raises(RuntimeError, match="forced scoring failure"):
        with _prepare_m8_generator_context(request) as generator:
            authority = generator._authority  # noqa: SLF001
            raise RuntimeError("forced scoring failure")

    assert authority is not None
    with pytest.raises(ValueError, match="no longer active"):
        authority.require_active()


def test_event_major_batches_own_one_snapshot_and_one_common_capability(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    from yieldforge.baseline import replay
    from yieldforge.oracle import certificates, checker, sparse

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0, event_count=4)
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    snapshot_count = 0
    common_count = 0
    authority_fingerprint_count = 0
    generator_fingerprint_count = 0
    checker_fingerprint_count = 0
    initial_registry_size = len(certificates._VALIDATED_COMMON_REGISTRY)  # noqa: SLF001
    maximum_live_common_count = 0
    original_snapshot = replay.snapshot_m7_replay_runtime
    original_common = certificates.build_validated_m8_common_transition_in_context
    original_authority_fingerprint = replay._authoritative_proof_runtime_fingerprint  # noqa: SLF001
    original_generator_fingerprint = sparse._generator_context_fingerprint  # noqa: SLF001
    original_checker_fingerprint = checker._checker_context_fingerprint  # noqa: SLF001

    def counted_snapshot(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal snapshot_count
        snapshot_count += 1
        return original_snapshot(*args, **kwargs)

    def counted_common(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal common_count, maximum_live_common_count
        common_count += 1
        result = original_common(*args, **kwargs)
        maximum_live_common_count = max(
            maximum_live_common_count,
            len(certificates._VALIDATED_COMMON_REGISTRY) - initial_registry_size,  # noqa: SLF001
        )
        return result

    def counted_authority_fingerprint(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal authority_fingerprint_count
        authority_fingerprint_count += 1
        return original_authority_fingerprint(*args, **kwargs)

    def counted_generator_fingerprint(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal generator_fingerprint_count
        generator_fingerprint_count += 1
        return original_generator_fingerprint(*args, **kwargs)

    def counted_checker_fingerprint(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal checker_fingerprint_count
        checker_fingerprint_count += 1
        return original_checker_fingerprint(*args, **kwargs)

    monkeypatch.setattr(replay, "snapshot_m7_replay_runtime", counted_snapshot)
    monkeypatch.setattr(
        replay,
        "_authoritative_proof_runtime_fingerprint",
        counted_authority_fingerprint,
    )
    monkeypatch.setattr(sparse, "_generator_context_fingerprint", counted_generator_fingerprint)
    monkeypatch.setattr(checker, "_checker_context_fingerprint", counted_checker_fingerprint)
    monkeypatch.setattr(sparse, "build_validated_m8_common_transition_in_context", counted_common)
    monkeypatch.setattr(checker, "build_validated_m8_common_transition_in_context", counted_common)

    result = sparse.score_sparse_event(request)
    assert snapshot_count == 1
    assert common_count == 3
    assert maximum_live_common_count == 1
    assert authority_fingerprint_count == 5
    assert generator_fingerprint_count == 4
    assert checker_fingerprint_count == 0

    checks = checker.check_action_proofs(request, result.proofs)
    assert all(item.valid for item in checks)
    assert snapshot_count == 2
    assert common_count == 6
    assert maximum_live_common_count == 1
    assert authority_fingerprint_count == 10
    assert generator_fingerprint_count == 4
    assert checker_fingerprint_count == 4


def test_jagua_batch_materializes_one_bound_copy_and_one_lease(
    monkeypatch,
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    from tests.oracle.test_certificates import _write_collision_jagua
    from yieldforge.baseline import replay
    from yieldforge.oracle.checker import check_action_proofs

    executable = tmp_path / "fake-jagua"
    _write_collision_jagua(executable, collision=False)
    runtime = two_problem_runtime(
        first_width=9.0,
        second_width=4.0,
        event_count=4,
        collision_backend="jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
        jagua_executable=executable,
        jagua_differential_audit=True,
    )
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    original_materialize = replay._materialize_private_jagua_file  # noqa: SLF001
    materialized = []

    def counted_materialize(*args, **kwargs):  # type: ignore[no-untyped-def]
        result = original_materialize(*args, **kwargs)
        materialized.append((kwargs["prefix"], result.path))
        return result

    monkeypatch.setattr(
        replay,
        "_materialize_private_jagua_file",
        counted_materialize,
    )

    sparse = score_sparse_event(request)
    assert [prefix for prefix, _path in materialized] == ["bound", "proof"]
    assert all(not path.exists() for _prefix, path in materialized)

    materialized.clear()
    assert all(result.valid for result in check_action_proofs(request, sparse.proofs))
    assert [prefix for prefix, _path in materialized] == ["bound", "proof"]
    assert all(not path.exists() for _prefix, path in materialized)
