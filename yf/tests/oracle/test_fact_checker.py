from __future__ import annotations

import json
import pickle
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

import pytest
from shapely import Polygon

from tests.oracle.fixtures import (
    exhaustive_certificate_cases,
    inventory_item,
    two_problem_runtime,
)
from yieldforge.baseline.policies import M7PolicyName
from yieldforge.baseline.replay import (
    apply_m7_action_descriptor,
    authoritative_m7_proof_runtime,
    enumerate_m7_action_catalog,
    initial_m7_cursor,
    m7_cursor_sha256,
    m7_semantic_runtime_sha256,
    select_m7_fallback,
    snapshot_m7_replay_runtime,
)
from yieldforge.oracle import facts
from yieldforge.oracle.concurrency import activate_m8_translation_audit_processes
from yieldforge.oracle.fact_checker import (
    M8CommonFactCheckRequest,
    check_m8_common_fact_bundle,
)
from yieldforge.oracle.factored import (
    M8UncheckedBundleRequest,
    score_unchecked_fact_bundle,
)
from yieldforge.oracle.proofs import m8_suffix_sha256
from yieldforge.oracle.reference import M8OracleRequest
from yieldforge.oracle.visibility import FullRealizedVisibility
from yieldforge.temporal_benchmark.contracts import FeasibilityRateManifest

_FREEZE_ID = "yfm7freeze-" + "b" * 24
_FREEZE_SHA256 = "sha256:" + "b" * 64


def _check_request(
    unchecked: M8UncheckedBundleRequest,
    semantic_bytes: bytes,
) -> M8CommonFactCheckRequest:
    oracle_request = unchecked.oracle_request
    runtime = oracle_request.runtime
    cursor = oracle_request.cursor
    catalog = enumerate_m7_action_catalog(runtime, cursor=cursor)
    visible = oracle_request.visibility.visible_suffix(
        current_position=catalog.event_position,
    )
    stop = catalog.event_position + 1 + len(visible)
    semantic_runtime_sha256 = m7_semantic_runtime_sha256(runtime)
    return M8CommonFactCheckRequest(
        semantic_bundle_bytes=semantic_bytes,
        oracle_request=oracle_request,
        expected_semantic_runtime_sha256=semantic_runtime_sha256,
        expected_current_cursor_sha256=m7_cursor_sha256(cursor),
        expected_catalog_event_position=catalog.event_position,
        expected_catalog_action_ids=tuple(item.action_id for item in catalog.actions),
        expected_stop_event_position=stop,
        expected_suffix_sha256=m8_suffix_sha256(
            semantic_runtime_sha256=semantic_runtime_sha256,
            start_event_position=catalog.event_position,
            stop_event_position=stop,
            bindings=visible,
        ),
        expected_freeze_id=unchecked.freeze_id,
        expected_freeze_sha256=unchecked.freeze_sha256,
    )


def _replace_refs(value, replacements: dict[str, str]):  # type: ignore[no-untyped-def]
    if isinstance(value, dict):
        for key, item in tuple(value.items()):
            value[key] = _replace_refs(item, replacements)
        return value
    if isinstance(value, list):
        return [_replace_refs(item, replacements) for item in value]
    if isinstance(value, str):
        return replacements.get(value, value)
    return value


def _rehash_payload(payload: dict[str, object]) -> bytes:
    replacements: dict[str, str] = {}
    layer_names = (
        "translation_batches",
        "candidate_scalar_facts",
        "frontier_facts",
        "standard_candidate_facts",
        "common_lemmas",
        "influence_facts",
        "action_roots",
    )
    for layer_name in layer_names:
        entries = payload[layer_name]
        assert isinstance(entries, list)
        for entry in entries:
            assert isinstance(entry, dict)
            _replace_refs(entry, replacements)
            old = entry["fact_sha256"]
            assert isinstance(old, str)
            kind = entry["fact_kind"]
            assert isinstance(kind, str)
            new = facts.m8_fact_sha256(kind, entry)
            entry["fact_sha256"] = new
            replacements[old] = new
        if layer_name in {
            "translation_batches",
            "candidate_scalar_facts",
            "frontier_facts",
        }:
            entries.sort(key=lambda item: item["fact_sha256"])
        elif layer_name == "standard_candidate_facts":
            entries.sort(
                key=lambda item: (
                    item["stream_id"],
                    item["event_position"],
                    item["profile_position"],
                    item["fact_sha256"],
                )
            )
        elif layer_name == "common_lemmas":
            entries.sort(
                key=lambda item: (
                    item["stream_id"],
                    item["event_position"],
                    item["fact_sha256"],
                )
            )
        elif layer_name == "influence_facts":
            entries.sort(
                key=lambda item: (
                    item["stream_id"],
                    item["event_position"],
                    item["root_action_id"],
                    item["fact_sha256"],
                )
            )
        else:
            entries.sort(
                key=lambda item: (
                    item["stream_id"],
                    item["action_id"],
                    item["fact_sha256"],
                )
            )
    payload["bundle_sha256"] = facts.m8_bundle_sha256(payload)
    return facts.canonical_semantic_json(payload)


def _unchecked_and_check_request() -> tuple[
    M8UncheckedBundleRequest,
    M8CommonFactCheckRequest,
]:
    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    cursor = initial_m7_cursor(runtime.replay_input)
    oracle_request = M8OracleRequest(
        runtime=runtime,
        cursor=cursor,
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    unchecked = M8UncheckedBundleRequest(
        oracle_request=oracle_request,
        freeze_id=_FREEZE_ID,
        freeze_sha256=_FREEZE_SHA256,
    )
    generated = score_unchecked_fact_bundle(unchecked)
    return unchecked, _check_request(unchecked, generated.semantic_bytes)


def _shift_utc(value: str, *, seconds: int = 1) -> str:
    return facts.encode_canonical_utc(
        facts.decode_canonical_utc(value) + timedelta(seconds=seconds)
    )


def _increment_f64(value: str, *, amount: float = 1.0) -> str:
    return facts.encode_canonical_f64(facts.decode_canonical_f64(value) + amount)


def _semantic_field_paths(value, path=()):  # type: ignore[no-untyped-def]
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "fact_sha256":
                continue
            field_path = (*path, key)
            yield field_path, item
            if isinstance(item, (dict, list)):
                yield from _semantic_field_paths(item, field_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            item_path = (*path, index)
            if isinstance(item, (dict, list)):
                yield from _semantic_field_paths(item, item_path)


def _set_semantic_path(root, path, value) -> None:  # type: ignore[no-untyped-def]
    target = root
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = value


def _mutated_semantic_value(path, value):  # type: ignore[no-untyped-def]
    key = path[-1]
    if isinstance(value, dict):
        return {**deepcopy(value), "unexpected_mutated_field": True}
    if isinstance(value, list):
        if not value:
            return ["mutated"]
        if len(value) == 1:
            return [*deepcopy(value), deepcopy(value[0])]
        return list(reversed(deepcopy(value)))
    if value is None:
        if key in {"previous_release", "entered_at"}:
            return "2026-01-01T00:00:01.000000Z"
        if key == "materialized_action_id":
            return "yfm7a-" + "e" * 24
        if key in {"selected_remnant_id", "parent_remnant_id"}:
            return "yfrm-" + "e" * 24
        if key.endswith("_ref") or key.endswith("_sha256"):
            return "sha256:" + "e" * 64
        return "mutated"
    if type(value) is bool:
        return not value
    if type(value) is int:
        return value + 1
    assert type(value) is str
    if value.startswith("sha256:"):
        replacement = "0" if value[7] != "0" else "1"
        return f"sha256:{replacement}{value[8:]}"
    if value.startswith("f64:"):
        return _increment_f64(value)
    if value.endswith("Z") and "T" in value:
        return _shift_utc(value)
    if len(value) == 64 and all(character in "0123456789abcdef" for character in value):
        replacement = "0" if value[0] != "0" else "1"
        return replacement + value[1:]
    for prefix in ("yfm7a-", "yfm7e-", "yfm7b-", "yfrm-", "yfts-", "yfm7p-", "yfm7c-"):
        if value.startswith(prefix):
            replacement = "0" if value[-1] != "0" else "1"
            return value[:-1] + replacement
    if key == "wkb_hex":
        replacement = "0" if value[-1] != "0" else "1"
        return value[:-1] + replacement
    if key == "provenance" and value in {"observed", "generated", "assumed"}:
        return "generated" if value != "generated" else "assumed"
    return value + "-mutated"


def test_common_checker_strict_loads_and_validates_without_producer_authority(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    unchecked, request = _unchecked_and_check_request()
    from yieldforge.oracle import certificates, factored

    def forbidden(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("fact checker invoked producer or trusted derivation authority")

    for module, name in (
        (factored, "score_unchecked_fact_bundle"),
        (certificates, "certify_event_passivity"),
        (certificates, "_derive_m8_common_transition_fact"),
        (certificates, "_derive_m8_common_transition_fact_unprofiled"),
        (certificates, "_derive_m8_common_transition_fact_authoritative"),
        (certificates, "_capture_unchecked_m8_common_transition"),
        (certificates, "_capture_unchecked_event_passivity"),
        (certificates, "build_validated_m8_common_transition"),
        (certificates, "build_validated_m8_common_transition_in_context"),
        (certificates, "validate_m8_common_transition_fact"),
    ):
        monkeypatch.setattr(module, name, forbidden)

    result = check_m8_common_fact_bundle(request)

    assert result.valid
    assert result.failure_code == "valid_common_facts"
    assert result.checked_common_lemma_count == 1
    assert result.issued_common_capability_count == 1
    assert result.exact_replay_fallback_count == 0
    assert result.first_failing_fact_sha256 is None
    assert result.authority_mode == "checked_common_only"
    assert not hasattr(result, "decision")
    assert unchecked.oracle_request.runtime is request.oracle_request.runtime


def test_stateful_visibility_cannot_swap_frozen_request_claims_mid_check() -> None:
    class ClaimSwappingVisibility:
        mode = "full_realized_future"

        def __init__(self, stream):  # type: ignore[no-untyped-def]
            self.stream = stream
            self.request = None

        def visible_suffix(self, *, current_position):  # type: ignore[no-untyped-def]
            assert self.request is not None
            object.__setattr__(self.request, "expected_freeze_id", _FREEZE_ID)
            object.__setattr__(self.request, "expected_freeze_sha256", _FREEZE_SHA256)
            object.__setattr__(self.request, "semantic_bundle_bytes", b"{}")
            return self.stream[current_position + 1 :]

    unchecked, valid_request = _unchecked_and_check_request()
    visibility = ClaimSwappingVisibility(unchecked.oracle_request.runtime.replay_input.instances)
    oracle_request = replace(unchecked.oracle_request, visibility=visibility)
    wrong_freeze_id = "yfm7freeze-" + "a" * 24
    wrong_freeze_sha256 = "sha256:" + "a" * 64
    request = replace(
        valid_request,
        oracle_request=oracle_request,
        expected_freeze_id=wrong_freeze_id,
        expected_freeze_sha256=wrong_freeze_sha256,
    )
    visibility.request = request

    result = check_m8_common_fact_bundle(request)

    assert not result.valid
    assert result.failure_code == "freeze_binding_mismatch"
    assert result.first_failing_fact_sha256 is not None


def test_request_capture_cannot_bless_a_transient_claim_swap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import fact_checker

    _unchecked, valid_request = _unchecked_and_check_request()
    wrong_freeze_id = "yfm7freeze-" + "a" * 24
    wrong_freeze_sha256 = "sha256:" + "a" * 64
    request = replace(
        valid_request,
        expected_freeze_id=wrong_freeze_id,
        expected_freeze_sha256=wrong_freeze_sha256,
    )
    original_replace = fact_checker.replace

    def racing_replace(value, /, **changes):  # type: ignore[no-untyped-def]
        if type(value) is M8CommonFactCheckRequest:
            object.__setattr__(value, "expected_freeze_id", _FREEZE_ID)
            object.__setattr__(value, "expected_freeze_sha256", _FREEZE_SHA256)
            try:
                return original_replace(value, **changes)
            finally:
                object.__setattr__(value, "expected_freeze_id", wrong_freeze_id)
                object.__setattr__(
                    value,
                    "expected_freeze_sha256",
                    wrong_freeze_sha256,
                )
        return original_replace(value, **changes)

    monkeypatch.setattr(fact_checker, "replace", racing_replace)

    result = check_m8_common_fact_bundle(request)

    assert not result.valid
    assert result.failure_code == "freeze_binding_mismatch"
    assert result.issued_common_capability_count == 0


def test_generic_common_registration_cannot_issue_authority_without_private_boundary() -> None:
    from yieldforge.oracle import certificates

    unchecked, _request = _unchecked_and_check_request()
    oracle_request = unchecked.oracle_request
    runtime = oracle_request.runtime
    catalog = enumerate_m7_action_catalog(runtime, cursor=oracle_request.cursor)
    selected = select_m7_fallback(catalog, policy=runtime.replay_input.policy)
    descriptor = next(item for item in catalog.actions if item.action_id == selected.action_id)
    initial = apply_m7_action_descriptor(
        runtime,
        cursor=oracle_request.cursor,
        catalog=catalog,
        descriptor=descriptor,
        decision_key=selected.decision_key,
    )
    fact = certificates.build_m8_common_transition_fact(runtime, cursor=initial.cursor)
    snapshot = snapshot_m7_replay_runtime(runtime)
    issued = None
    try:
        with pytest.raises(TypeError):
            issued = certificates._register_validated_common_transition(  # noqa: SLF001
                fact,
                snapshot,
            )
    finally:
        if issued is not None:
            certificates._release_validated_common_transition(issued)  # noqa: SLF001
        else:
            snapshot.close()


def test_checker_registration_token_is_unpickleable_exact_bound_and_one_shot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import certificates, fact_checker

    unchecked, _request = _unchecked_and_check_request()
    runtime = unchecked.oracle_request.runtime
    cursor = unchecked.oracle_request.cursor
    catalog = enumerate_m7_action_catalog(runtime, cursor=cursor)
    selected = select_m7_fallback(catalog, policy=runtime.replay_input.policy)
    descriptor = next(item for item in catalog.actions if item.action_id == selected.action_id)
    initial = apply_m7_action_descriptor(
        runtime,
        cursor=cursor,
        catalog=catalog,
        descriptor=descriptor,
        decision_key=selected.decision_key,
    )
    fact = certificates.build_m8_common_transition_fact(runtime, cursor=initial.cursor)
    token_registry_before = dict(fact_checker._CHECKER_REGISTRATION_TOKENS)  # noqa: SLF001

    with authoritative_m7_proof_runtime(runtime) as authority:
        with fact_checker._checker_registration_scope(  # noqa: SLF001
            authority,
            (fact,),
        ) as token:
            with pytest.raises(TypeError, match="cannot be serialized"):
                pickle.dumps(token)
            forged = fact_checker._M8FactCheckerRegistrationToken(object())  # noqa: SLF001
            with pytest.raises(ValueError, match="invalid or inactive"):
                fact_checker._consume_checker_registration_token(  # noqa: SLF001
                    forged,
                    authority,
                    fact,
                )
            original_pid = fact_checker.os.getpid()
            with monkeypatch.context() as local_patch:
                local_patch.setattr(fact_checker.os, "getpid", lambda: original_pid + 1)
                with pytest.raises(ValueError, match="invalid or inactive"):
                    fact_checker._consume_checker_registration_token(  # noqa: SLF001
                        token,
                        authority,
                        fact,
                    )
            fact_checker._consume_checker_registration_token(  # noqa: SLF001
                token,
                authority,
                fact,
            )
            with pytest.raises(ValueError, match="already consumed"):
                fact_checker._consume_checker_registration_token(  # noqa: SLF001
                    token,
                    authority,
                    fact,
                )

    assert fact_checker._CHECKER_REGISTRATION_TOKENS == token_registry_before  # noqa: SLF001


def test_checker_registration_and_release_failure_leave_registries_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import certificates, fact_checker

    _unchecked, request = _unchecked_and_check_request()
    token_registry_before = dict(fact_checker._CHECKER_REGISTRATION_TOKENS)  # noqa: SLF001
    common_registry_before = dict(certificates._VALIDATED_COMMON_REGISTRY)  # noqa: SLF001
    original_release = fact_checker._release_validated_common_transition  # noqa: SLF001

    def release_then_fail(common):  # type: ignore[no-untyped-def]
        original_release(common)
        raise RuntimeError("injected post-release integrity failure")

    monkeypatch.setattr(
        fact_checker,
        "_release_validated_common_transition",
        release_then_fail,
    )

    result = check_m8_common_fact_bundle(request)

    assert not result.valid
    assert result.failure_code == "capability_registration_failure"
    assert fact_checker._CHECKER_REGISTRATION_TOKENS == token_registry_before  # noqa: SLF001
    assert certificates._VALIDATED_COMMON_REGISTRY == common_registry_before  # noqa: SLF001


@pytest.mark.parametrize(
    "alias",
    (
        lambda value: value + b"\n",
        lambda value: b" " + value,
        lambda value: value.replace(
            b'{"action_roots"',
            b'{"schema_version":"yieldforge.m8-unchecked-fact-bundle.v2","action_roots"',
            1,
        ),
        lambda value: json.dumps(
            json.loads(value),
            separators=(",", ":"),
            sort_keys=False,
        ).encode(),
    ),
)
def test_common_checker_rejects_every_noncanonical_json_transport_alias(alias) -> None:  # type: ignore[no-untyped-def]
    _unchecked, request = _unchecked_and_check_request()
    aliased = alias(request.semantic_bundle_bytes)
    if aliased == request.semantic_bundle_bytes:
        aliased = b'{"z":0,' + request.semantic_bundle_bytes[1:]

    result = check_m8_common_fact_bundle(replace(request, semantic_bundle_bytes=aliased))

    assert not result.valid
    assert result.failure_code == "noncanonical_bundle"


def test_rehashed_scalar_source_claim_cannot_create_semantic_authority() -> None:
    _unchecked, request = _unchecked_and_check_request()
    payload = deepcopy(json.loads(request.semantic_bundle_bytes))
    scalar = payload["candidate_scalar_facts"][0]
    scalar["source_transform_sha256"] = "sha256:" + "f" * 64
    semantic_bytes = _rehash_payload(payload)
    mutated = next(
        item
        for item in payload["candidate_scalar_facts"]
        if item["source_transform_sha256"] == "sha256:" + "f" * 64
    )

    result = check_m8_common_fact_bundle(replace(request, semantic_bundle_bytes=semantic_bytes))

    assert not result.valid
    assert result.failure_code == "candidate_scalar_mismatch"
    assert result.first_failing_fact_sha256 == mutated["fact_sha256"]


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("event_net_cost_bits", "f64:8000000000000000"),
        ("event_occurred_at", "2026-01-01T00:00:00Z"),
    ),
)
def test_parse_time_semantic_failure_reports_submitted_owner_fact(
    field: str,
    value: str,
) -> None:
    _unchecked, request = _unchecked_and_check_request()
    payload = deepcopy(json.loads(request.semantic_bundle_bytes))
    payload["common_lemmas"][0][field] = value
    semantic_bytes = _rehash_payload(payload)
    owner_sha256 = payload["common_lemmas"][0]["fact_sha256"]

    result = check_m8_common_fact_bundle(replace(request, semantic_bundle_bytes=semantic_bytes))

    assert not result.valid
    assert result.failure_code == "noncanonical_bundle"
    assert result.first_failing_fact_sha256 == owner_sha256


def test_direct_node_hash_mismatch_reports_submitted_owner_fact() -> None:
    _unchecked, request = _unchecked_and_check_request()
    payload = deepcopy(json.loads(request.semantic_bundle_bytes))
    scalar = payload["candidate_scalar_facts"][0]
    owner_sha256 = scalar["fact_sha256"]
    scalar["source_transform_sha256"] = "sha256:" + "e" * 64
    semantic_bytes = facts.canonical_semantic_json(payload)

    result = check_m8_common_fact_bundle(replace(request, semantic_bundle_bytes=semantic_bytes))

    assert not result.valid
    assert result.failure_code == "noncanonical_bundle"
    assert result.first_failing_fact_sha256 == owner_sha256


def test_self_consistent_rehashed_nonwinner_cannot_become_policy_minimum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.baseline.policies import (
        PolicyRank,
        PolicySelection,
        rank_policy_action,
    )
    from yieldforge.baseline.replay import select_m7_fallback as exact_select
    from yieldforge.oracle import certificates, sparse

    case = next(
        item
        for item in exhaustive_certificate_cases()
        if item.case_id == "myopic_geometry-one-match-fit-unequal-separated-three"
    )
    runtime = case.request.runtime
    oracle_request = replace(
        case.request,
        visibility=FullRealizedVisibility(runtime.replay_input.instances[:2]),
    )
    unchecked = M8UncheckedBundleRequest(
        oracle_request=oracle_request,
        freeze_id=_FREEZE_ID,
        freeze_sha256=_FREEZE_SHA256,
    )

    def forged_rank(policy, context):  # type: ignore[no-untyped-def]
        actual = rank_policy_action(policy, context)
        forged_primary = -100.0 if context.candidate_id == "candidate-two" else 0.0
        return PolicyRank(
            policy=actual.policy,
            comparison_key=(forged_primary, *actual.comparison_key[1:]),
            decision_key=actual.decision_key,
        )

    def forged_future_selection(catalog, *, policy):  # type: ignore[no-untyped-def]
        if catalog.event_position == 0:
            return exact_select(catalog, policy=policy)
        context = next(
            item
            for item in catalog.contexts
            if item.candidate_id == "candidate-two"
            and item.selected_stock_id == "current_standard_sheet"
        )
        policy_name = policy.name if hasattr(policy, "name") else policy
        rank = forged_rank(policy_name, context)
        return PolicySelection(action_id=context.action_id, decision_key=rank.decision_key)

    with monkeypatch.context() as producer_patch:
        producer_patch.setattr(certificates, "rank_policy_action", forged_rank)
        producer_patch.setattr(
            certificates,
            "select_m7_fallback",
            forged_future_selection,
        )
        producer_patch.setattr(sparse, "select_m7_fallback", forged_future_selection)
        generated = score_unchecked_fact_bundle(unchecked)

    payload = deepcopy(json.loads(generated.semantic_bytes))
    semantic_bytes = _rehash_payload(payload)
    submitted = facts.M8UncheckedFactBundleV2.model_validate_json(
        semantic_bytes,
        strict=True,
    )
    lemma = submitted.common_lemmas[0]
    assert lemma.selected_candidate_id == "candidate-two"
    submitted_nonwinner = next(
        item
        for item in submitted.standard_candidate_facts
        if item.event_position == lemma.event_position and item.candidate_id == "candidate-two"
    )
    first_submitted_profile = next(
        item
        for item in submitted.standard_candidate_facts
        if item.event_position == lemma.event_position and item.profile_position == 0
    )
    assert lemma.minimum_standard_candidate_ref == submitted_nonwinner.fact_sha256

    result = check_m8_common_fact_bundle(
        replace(
            _check_request(unchecked, semantic_bytes),
            allow_exact_replay=True,
        )
    )

    assert not result.valid
    assert result.failure_code == "standard_profile_mismatch"
    assert result.first_failing_fact_sha256 == first_submitted_profile.fact_sha256
    assert result.exact_replay_fallback_count == 1


def test_structural_dangling_common_reference_reports_the_common_owner() -> None:
    _unchecked, request = _unchecked_and_check_request()
    payload = deepcopy(json.loads(request.semantic_bundle_bytes))
    lemma = payload["common_lemmas"][0]
    dangling = "sha256:" + "c" * 64
    lemma["candidate_scalar_refs"] = [dangling]
    for classification in lemma["inventory_classifications"]:
        classification["candidate_scalar_refs"] = [dangling]
    semantic_bytes = _rehash_payload(payload)
    owner_sha256 = payload["common_lemmas"][0]["fact_sha256"]

    result = check_m8_common_fact_bundle(replace(request, semantic_bundle_bytes=semantic_bytes))

    assert not result.valid
    assert result.failure_code == "m8_dangling_reference"
    assert result.first_failing_fact_sha256 == owner_sha256


def test_structural_unused_rehashed_fact_reports_the_unused_owner() -> None:
    _unchecked, request = _unchecked_and_check_request()
    payload = deepcopy(json.loads(request.semantic_bundle_bytes))
    unused = deepcopy(payload["candidate_scalar_facts"][0])
    unused["candidate_id"] = "unused-candidate"
    unused["fact_sha256"] = facts.m8_fact_sha256(unused["fact_kind"], unused)
    payload["candidate_scalar_facts"].append(unused)
    semantic_bytes = _rehash_payload(payload)
    owner_sha256 = next(
        item["fact_sha256"]
        for item in payload["candidate_scalar_facts"]
        if item["candidate_id"] == "unused-candidate"
    )

    result = check_m8_common_fact_bundle(replace(request, semantic_bundle_bytes=semantic_bytes))

    assert not result.valid
    assert result.failure_code == "m8_unused_fact"
    assert result.first_failing_fact_sha256 == owner_sha256


def test_structural_cross_runtime_fact_reports_the_foreign_owner() -> None:
    _unchecked, request = _unchecked_and_check_request()
    payload = deepcopy(json.loads(request.semantic_bundle_bytes))
    payload["candidate_scalar_facts"][0]["semantic_runtime_sha256"] = "sha256:" + "a" * 64
    semantic_bytes = _rehash_payload(payload)
    owner_sha256 = payload["candidate_scalar_facts"][0]["fact_sha256"]

    result = check_m8_common_fact_bundle(replace(request, semantic_bundle_bytes=semantic_bytes))

    assert not result.valid
    assert result.failure_code == "m8_context_mismatch"
    assert result.first_failing_fact_sha256 == owner_sha256


def test_structural_reordered_fixed_layer_reports_the_first_out_of_order_fact() -> None:
    _unchecked, request = _unchecked_and_check_request()
    payload = deepcopy(json.loads(request.semantic_bundle_bytes))
    payload["action_roots"].reverse()
    payload["bundle_sha256"] = facts.m8_bundle_sha256(payload)
    semantic_bytes = facts.canonical_semantic_json(payload)
    owner_sha256 = payload["action_roots"][0]["fact_sha256"]

    result = check_m8_common_fact_bundle(replace(request, semantic_bundle_bytes=semantic_bytes))

    assert not result.valid
    assert result.failure_code == "m8_fixed_layer_order"
    assert result.first_failing_fact_sha256 == owner_sha256


@pytest.mark.parametrize(
    "mutation",
    ("storage_start", "event_end", "cursor_previous_release", "cursor_sequence", "costs"),
)
def test_correlated_portable_time_cost_and_cursor_mutations_cannot_rehash_to_truth(
    mutation: str,
) -> None:
    _unchecked, request = _unchecked_and_check_request()
    payload = deepcopy(json.loads(request.semantic_bundle_bytes))
    lemma = payload["common_lemmas"][0]
    transition = lemma["portable_transition"]
    event = transition["event"]
    cursor_before = transition["cursor_before"]
    cursor_after = transition["cursor_after"]
    if mutation == "storage_start":
        changed = _shift_utc(lemma["storage_interval_start"])
        lemma["storage_interval_start"] = changed
        event["storage_interval_start"] = changed
        cursor_before["current_time"] = changed
    elif mutation == "event_end":
        changed = _shift_utc(lemma["event_occurred_at"])
        lemma["event_occurred_at"] = changed
        lemma["storage_interval_end"] = changed
        lemma["cursor_current_time"] = changed
        lemma["cursor_previous_release"] = changed
        event["occurred_at"] = changed
        event["storage_interval_end"] = changed
        cursor_after["current_time"] = changed
        cursor_after["previous_release"] = changed
    elif mutation == "cursor_previous_release":
        cursor_before["previous_release"] = _shift_utc(cursor_before["previous_release"])
    elif mutation == "cursor_sequence":
        event["timestamp_subsequence"] += 1
        cursor_after["timestamp_subsequence"] += 1
    else:
        for ledger in (
            event["delta_costs"],
            event["cumulative_costs"],
            cursor_after["cumulative_costs"],
        ):
            ledger["storage_cost_bits"] = _increment_f64(ledger["storage_cost_bits"])
            ledger["net_cost_bits"] = _increment_f64(ledger["net_cost_bits"])
        lemma["event_net_cost_bits"] = event["delta_costs"]["net_cost_bits"]
    semantic_bytes = _rehash_payload(payload)
    owner_sha256 = payload["common_lemmas"][0]["fact_sha256"]
    facts.M8UncheckedFactBundleV2.model_validate_json(semantic_bytes, strict=True)

    first = check_m8_common_fact_bundle(replace(request, semantic_bundle_bytes=semantic_bytes))
    second = check_m8_common_fact_bundle(replace(request, semantic_bundle_bytes=semantic_bytes))

    assert not first.valid
    assert first.failure_code == "portable_transition_mismatch"
    assert first.first_failing_fact_sha256 == owner_sha256
    assert second.failure_code == first.failure_code
    assert second.first_failing_fact_sha256 == first.first_failing_fact_sha256


def test_correlated_action_identity_mutation_cannot_rehash_to_authority() -> None:
    _unchecked, request = _unchecked_and_check_request()
    payload = deepcopy(json.loads(request.semantic_bundle_bytes))
    lemma = payload["common_lemmas"][0]
    transition = lemma["portable_transition"]
    old_action_id = lemma["selected_materialized_action_id"]
    changed_action_id = "yfm7a-" + "c" * 24
    lemma["selected_materialized_action_id"] = changed_action_id
    transition["action_binding"]["materialized_action_id"] = changed_action_id
    transition["event"]["action"]["action_id"] = changed_action_id
    selected_standard = next(
        item
        for item in payload["standard_candidate_facts"]
        if item["fact_sha256"] == lemma["minimum_standard_candidate_ref"]
    )
    selected_standard["materialized_action_id"] = changed_action_id
    for influence in payload["influence_facts"]:
        if influence["common_materialized_action_id"] == old_action_id:
            influence["common_materialized_action_id"] = changed_action_id
        if influence["branch_materialized_action_id"] == old_action_id:
            influence["branch_materialized_action_id"] = changed_action_id
    semantic_bytes = _rehash_payload(payload)
    owner_sha256 = next(
        item["fact_sha256"]
        for item in payload["standard_candidate_facts"]
        if item["candidate_id"] == selected_standard["candidate_id"]
    )
    facts.M8UncheckedFactBundleV2.model_validate_json(semantic_bytes, strict=True)

    result = check_m8_common_fact_bundle(replace(request, semantic_bundle_bytes=semantic_bytes))

    assert not result.valid
    assert result.failure_code == "standard_profile_mismatch"
    assert result.first_failing_fact_sha256 == owner_sha256


def test_every_common_reachable_semantic_field_mutation_fails_stably_with_an_owner() -> None:
    _unchecked, request = _unchecked_and_check_request()
    original = json.loads(request.semantic_bundle_bytes)
    layers = (
        "candidate_scalar_facts",
        "frontier_facts",
        "standard_candidate_facts",
        "common_lemmas",
    )
    checked_fields = 0
    failures: list[str] = []
    for layer_name in layers:
        for entry_index, original_entry in enumerate(original[layer_name]):
            for field_path, original_value in _semantic_field_paths(original_entry):
                checked_fields += 1
                payload = deepcopy(original)
                entry = payload[layer_name][entry_index]
                _set_semantic_path(
                    entry,
                    field_path,
                    _mutated_semantic_value(field_path, original_value),
                )
                semantic_bytes = _rehash_payload(payload)
                mutated_request = replace(request, semantic_bundle_bytes=semantic_bytes)
                first = check_m8_common_fact_bundle(mutated_request)
                second = check_m8_common_fact_bundle(mutated_request)
                label = f"{layer_name}[{entry_index}].{'.'.join(map(str, field_path))}"
                if first.valid:
                    failures.append(f"{label}: unexpectedly valid")
                elif first.first_failing_fact_sha256 is None:
                    failures.append(f"{label}: missing first owner ({first.failure_code})")
                elif (
                    second.failure_code != first.failure_code
                    or second.first_failing_fact_sha256 != first.first_failing_fact_sha256
                ):
                    failures.append(f"{label}: unstable {first.failure_code}/{second.failure_code}")
    assert checked_fields >= 250
    assert not failures, "\n".join(failures[:25])


def test_every_counted_translation_semantic_field_mutation_fails_stably() -> None:
    runtime = two_problem_runtime(
        first_width=9.0,
        second_width=4.0,
        policy=M7PolicyName.NET_COST,
        rates=FeasibilityRateManifest(
            purchase_cost_per_area=1.0,
            storage_cost_per_area_hour=0.0,
            return_handling_cost_per_remnant=0.0,
            retrieval_handling_cost_per_remnant=200.0,
            scrap_credit_per_area=0.0,
        ),
    )
    binding = runtime.replay_input.instances[1]
    area_only = inventory_item(
        Polygon(((0, 0), (4, 0), (4, 1), (1, 1), (1, 10), (0, 10))),
        material=binding.material,
        token="fact-checker-translation-fields",
    )
    cursor = replace(initial_m7_cursor(runtime.replay_input), inventory=(area_only,))
    oracle_request = M8OracleRequest(
        runtime=runtime,
        cursor=cursor,
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    unchecked = M8UncheckedBundleRequest(
        oracle_request=oracle_request,
        freeze_id=_FREEZE_ID,
        freeze_sha256=_FREEZE_SHA256,
    )
    generated = score_unchecked_fact_bundle(unchecked)
    request = _check_request(unchecked, generated.semantic_bytes)
    original = json.loads(generated.semantic_bytes)
    checked_fields = 0
    failures: list[str] = []
    for entry_index, original_entry in enumerate(original["translation_batches"]):
        for field_path, original_value in _semantic_field_paths(original_entry):
            checked_fields += 1
            payload = deepcopy(original)
            entry = payload["translation_batches"][entry_index]
            _set_semantic_path(
                entry,
                field_path,
                _mutated_semantic_value(field_path, original_value),
            )
            semantic_bytes = _rehash_payload(payload)
            mutated_request = replace(request, semantic_bundle_bytes=semantic_bytes)
            with activate_m8_translation_audit_processes(2):
                first = check_m8_common_fact_bundle(mutated_request)
                second = check_m8_common_fact_bundle(mutated_request)
            label = f"translation_batches[{entry_index}].{'.'.join(map(str, field_path))}"
            if first.valid:
                failures.append(f"{label}: unexpectedly valid")
            elif first.first_failing_fact_sha256 is None:
                failures.append(f"{label}: missing first owner ({first.failure_code})")
            elif (
                second.failure_code != first.failure_code
                or second.first_failing_fact_sha256 != first.first_failing_fact_sha256
            ):
                failures.append(f"{label}: unstable failure")
    assert checked_fields >= 15
    assert not failures, "\n".join(failures[:25])


def test_counted_common_is_audited_once_and_uses_zero_exact_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import fact_checker

    runtime = two_problem_runtime(
        first_width=9.0,
        second_width=4.0,
        policy=M7PolicyName.NET_COST,
        rates=FeasibilityRateManifest(
            purchase_cost_per_area=1.0,
            storage_cost_per_area_hour=0.0,
            return_handling_cost_per_remnant=0.0,
            retrieval_handling_cost_per_remnant=200.0,
            scrap_credit_per_area=0.0,
        ),
    )
    binding = runtime.replay_input.instances[1]
    area_only = inventory_item(
        Polygon(((0, 0), (4, 0), (4, 1), (1, 1), (1, 10), (0, 10))),
        material=binding.material,
        token="fact-checker-counted",
    )
    cursor = replace(initial_m7_cursor(runtime.replay_input), inventory=(area_only,))
    oracle_request = M8OracleRequest(
        runtime=runtime,
        cursor=cursor,
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    unchecked = M8UncheckedBundleRequest(
        oracle_request=oracle_request,
        freeze_id=_FREEZE_ID,
        freeze_sha256=_FREEZE_SHA256,
    )
    generated = score_unchecked_fact_bundle(unchecked)
    request = _check_request(unchecked, generated.semantic_bytes)
    counted = tuple(
        lemma for lemma in generated.bundle.common_lemmas if lemma.evidence_mode == "counted_no_fit"
    )
    assert len(counted) == 1

    audit_calls = 0
    audit_widths: list[int] = []
    exact_positions: list[int] = []
    original_audit = fact_checker.audit_layout_translation_batch
    original_catalog = fact_checker.enumerate_m7_action_catalog

    def counted_audit(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal audit_calls
        audit_calls += 1
        audit_widths.append(kwargs["process_count"])
        return original_audit(*args, **kwargs)

    def exact_catalog(runtime, *, cursor, **kwargs):  # type: ignore[no-untyped-def]
        exact_positions.append(cursor.next_event_position)
        return original_catalog(runtime, cursor=cursor, **kwargs)

    monkeypatch.setattr(fact_checker, "audit_layout_translation_batch", counted_audit)
    monkeypatch.setattr(fact_checker, "enumerate_m7_action_catalog", exact_catalog)

    with activate_m8_translation_audit_processes(2):
        result = check_m8_common_fact_bundle(request)

    assert result.valid
    assert result.counted_translation_audit_count == 1
    assert result.exact_replay_fallback_count == 0
    assert audit_calls == 1
    assert audit_widths == [2]
    assert exact_positions == [request.expected_catalog_event_position]


def test_counted_audit_telemetry_survives_a_later_semantic_failure() -> None:
    runtime = two_problem_runtime(
        first_width=9.0,
        second_width=4.0,
        policy=M7PolicyName.NET_COST,
        rates=FeasibilityRateManifest(
            purchase_cost_per_area=1.0,
            storage_cost_per_area_hour=0.0,
            return_handling_cost_per_remnant=0.0,
            retrieval_handling_cost_per_remnant=200.0,
            scrap_credit_per_area=0.0,
        ),
    )
    binding = runtime.replay_input.instances[1]
    area_only = inventory_item(
        Polygon(((0, 0), (4, 0), (4, 1), (1, 1), (1, 10), (0, 10))),
        material=binding.material,
        token="fact-checker-counted-failure",
    )
    cursor = replace(initial_m7_cursor(runtime.replay_input), inventory=(area_only,))
    oracle_request = M8OracleRequest(
        runtime=runtime,
        cursor=cursor,
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    unchecked = M8UncheckedBundleRequest(
        oracle_request=oracle_request,
        freeze_id=_FREEZE_ID,
        freeze_sha256=_FREEZE_SHA256,
    )
    generated = score_unchecked_fact_bundle(unchecked)
    payload = deepcopy(json.loads(generated.semantic_bytes))
    counted = next(
        item for item in payload["common_lemmas"] if item["evidence_mode"] == "counted_no_fit"
    )
    counted["legacy_common_fact_sha256"] = "sha256:" + "d" * 64
    semantic_bytes = _rehash_payload(payload)
    owner_sha256 = next(
        item["fact_sha256"]
        for item in payload["common_lemmas"]
        if item["event_position"] == counted["event_position"]
    )

    result = check_m8_common_fact_bundle(_check_request(unchecked, semantic_bytes))

    assert not result.valid
    assert result.failure_code == "portable_transition_mismatch"
    assert result.first_failing_fact_sha256 == owner_sha256
    assert result.counted_translation_audit_count == 1


def test_counted_checker_ignores_jagua_collision_masks_and_classifications(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    from yieldforge.baseline import replay
    from yieldforge.baseline.geometry import generate_layout_translations
    from yieldforge.baseline.jagua import JaguaGeneratedPrefilterResult
    from yieldforge.oracle import certificates, fact_checker

    jagua_path = tmp_path / "jagua-spike"
    jagua_path.write_bytes(b"frozen-test-binary")
    jagua_path.chmod(0o700)
    runtime = two_problem_runtime(
        first_width=9.0,
        second_width=4.0,
        policy=M7PolicyName.NET_COST,
        rates=FeasibilityRateManifest(
            purchase_cost_per_area=1.0,
            storage_cost_per_area_hour=0.0,
            return_handling_cost_per_remnant=0.0,
            retrieval_handling_cost_per_remnant=200.0,
            scrap_credit_per_area=0.0,
        ),
        collision_backend="jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
        jagua_executable=jagua_path,
    )
    binding = runtime.replay_input.instances[1]
    area_only = inventory_item(
        Polygon(((0, 0), (4, 0), (4, 1), (1, 1), (1, 10), (0, 10))),
        material=binding.material,
        token="fact-checker-jagua-independent",
    )
    cursor = replace(initial_m7_cursor(runtime.replay_input), inventory=(area_only,))
    candidates_by_id = {
        candidate.candidate_id: candidate
        for candidate_set in runtime.runtime_candidates.values()
        for candidate in candidate_set.candidates
    }

    def producer_prefilter(  # type: ignore[no-untyped-def]
        _executable,
        *,
        remnant,
        layouts,
        fit_config,
        search_config,
        container_guard,
    ):
        assert container_guard == 1.0
        source_remnant = area_only.remnant.model_copy(update={"remnant_id": remnant.remnant_id})
        batches = tuple(
            generate_layout_translations(
                source_remnant,
                candidates_by_id[layout.candidate_id],
                fit_config=fit_config,
                search_config=search_config,
                prepared_layout=layout,
                prepared_remnant=remnant,
            )
            for layout in layouts
        )
        return JaguaGeneratedPrefilterResult(
            translation_batches=batches,
            collision_masks=tuple((True,) * len(batch.translations) for batch in batches),
            guarded_query_count=sum(len(batch.translations) for batch in batches),
            jagua_rejection_count=0,
            build_microseconds=0,
            generation_microseconds=0,
            query_microseconds=0,
            wall_seconds=0.0,
        )

    oracle_request = M8OracleRequest(
        runtime=runtime,
        cursor=cursor,
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    unchecked = M8UncheckedBundleRequest(
        oracle_request=oracle_request,
        freeze_id=_FREEZE_ID,
        freeze_sha256=_FREEZE_SHA256,
    )
    with monkeypatch.context() as producer_patch:
        producer_patch.setattr(
            certificates,
            "run_jagua_generated_prefilter",
            producer_prefilter,
        )
        producer_patch.setattr(replay, "run_jagua_generated_prefilter", producer_prefilter)
        generated = score_unchecked_fact_bundle(unchecked)
        request = _check_request(unchecked, generated.semantic_bytes)
    assert any(item.evidence_mode == "counted_no_fit" for item in generated.bundle.common_lemmas)

    def forbidden_jagua(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("checker consulted untrusted Jagua collision classifications")

    monkeypatch.setattr(
        fact_checker,
        "run_jagua_generated_prefilter",
        forbidden_jagua,
    )
    monkeypatch.setattr(replay, "run_jagua_generated_prefilter", producer_prefilter)

    with activate_m8_translation_audit_processes(2):
        result = check_m8_common_fact_bundle(request)

    assert result.valid
    assert result.counted_translation_audit_count == 1
    assert result.exact_replay_fallback_count == 0


def test_exact_common_requires_explicit_permission_and_counts_full_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import fact_checker

    case = next(
        item
        for item in exhaustive_certificate_cases()
        if item.case_id == "myopic_geometry-zero-fit-equal-same-two"
    )
    unchecked = M8UncheckedBundleRequest(
        oracle_request=case.request,
        freeze_id=_FREEZE_ID,
        freeze_sha256=_FREEZE_SHA256,
    )
    generated = score_unchecked_fact_bundle(unchecked)
    assert tuple(item.evidence_mode for item in generated.bundle.common_lemmas) == ("exact_replay",)
    request = _check_request(unchecked, generated.semantic_bytes)
    exact_positions: list[int] = []
    original_catalog = fact_checker.enumerate_m7_action_catalog

    def exact_catalog(runtime, *, cursor, **kwargs):  # type: ignore[no-untyped-def]
        exact_positions.append(cursor.next_event_position)
        return original_catalog(runtime, cursor=cursor, **kwargs)

    monkeypatch.setattr(fact_checker, "enumerate_m7_action_catalog", exact_catalog)

    denied = check_m8_common_fact_bundle(request)
    allowed = check_m8_common_fact_bundle(replace(request, allow_exact_replay=True))

    assert not denied.valid
    assert denied.failure_code == "implicit_exact_replay"
    assert denied.exact_replay_fallback_count == 0
    assert allowed.valid
    assert allowed.exact_replay_fallback_count == 1
    assert allowed.exact_replay_fallback_wall_seconds > 0.0
    assert exact_positions == [
        request.expected_catalog_event_position,
        request.expected_catalog_event_position,
        request.expected_catalog_event_position + 1,
    ]


def test_failed_exact_fallback_is_still_counted_and_timed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import fact_checker

    case = next(
        item
        for item in exhaustive_certificate_cases()
        if item.case_id == "myopic_geometry-zero-fit-equal-same-two"
    )
    unchecked = M8UncheckedBundleRequest(
        oracle_request=case.request,
        freeze_id=_FREEZE_ID,
        freeze_sha256=_FREEZE_SHA256,
    )
    generated = score_unchecked_fact_bundle(unchecked)
    request = replace(
        _check_request(unchecked, generated.semantic_bytes),
        allow_exact_replay=True,
    )
    original_catalog = fact_checker.enumerate_m7_action_catalog

    def failing_exact_catalog(runtime, *, cursor, **kwargs):  # type: ignore[no-untyped-def]
        if cursor.next_event_position == request.expected_catalog_event_position + 1:
            raise RuntimeError("injected exact fallback failure")
        return original_catalog(runtime, cursor=cursor, **kwargs)

    monkeypatch.setattr(
        fact_checker,
        "enumerate_m7_action_catalog",
        failing_exact_catalog,
    )

    result = check_m8_common_fact_bundle(request)

    assert not result.valid
    assert result.failure_code == "internal_checker_failure"
    assert result.exact_replay_fallback_count == 1
    assert result.exact_replay_fallback_wall_seconds > 0.0


def test_missing_rejection_archive_is_a_declared_exact_unsupported_fallback() -> None:
    from yieldforge.oracle import certificates

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    inventory = inventory_item(
        Polygon(((0, 0), (4, 0), (4, 1), (1, 1), (1, 10), (0, 10))),
        material=binding.material,
        token="fact-checker-missing-archive",
    )
    cursor = replace(initial_m7_cursor(runtime.replay_input), inventory=(inventory,))
    oracle_request = M8OracleRequest(
        runtime=runtime,
        cursor=cursor,
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    unchecked = M8UncheckedBundleRequest(
        oracle_request=oracle_request,
        freeze_id=_FREEZE_ID,
        freeze_sha256=_FREEZE_SHA256,
    )
    generated = score_unchecked_fact_bundle(unchecked)

    old_runtime_sha256 = m7_semantic_runtime_sha256(runtime)
    verified = runtime.runtime_candidates[binding.problem_id]
    runtime.runtime_candidates[binding.problem_id] = replace(
        verified,
        rejection_layouts=(),
    )
    new_runtime_sha256 = m7_semantic_runtime_sha256(runtime)
    catalog = enumerate_m7_action_catalog(runtime, cursor=cursor)
    selected = select_m7_fallback(catalog, policy=runtime.replay_input.policy)
    descriptor = next(item for item in catalog.actions if item.action_id == selected.action_id)
    initial = apply_m7_action_descriptor(
        runtime,
        cursor=cursor,
        catalog=catalog,
        descriptor=descriptor,
        decision_key=selected.decision_key,
    )
    exact = certificates.build_m8_common_transition_fact(runtime, cursor=initial.cursor)

    payload = deepcopy(json.loads(generated.semantic_bytes))
    _replace_refs(payload, {old_runtime_sha256: new_runtime_sha256})
    lemma = payload["common_lemmas"][0]
    lemma["inventory_classifications"] = [
        {
            **item,
            "classification": "exact_survivor",
            "frontier_ref": None,
            "candidate_scalar_refs": [],
            "translation_batch_refs": [],
            "exact_replay_reason": "unsupported_representation",
        }
        for item in lemma["inventory_classifications"]
    ]
    lemma["candidate_scalar_refs"] = []
    lemma["frontier_refs"] = []
    lemma["translation_batch_refs"] = []
    lemma["evidence_mode"] = "exact_replay"
    lemma["exact_replay_reason"] = "exact_survivor_unsupported_representation"
    lemma["legacy_common_fact_sha256"] = exact.content_sha256
    lemma["portable_transition"] = certificates._portable_common_transition(  # noqa: SLF001
        exact
    ).model_dump(mode="json")
    payload["frontier_facts"] = []
    payload["translation_batches"] = []
    semantic_bytes = _rehash_payload(payload)

    result = check_m8_common_fact_bundle(
        replace(
            _check_request(unchecked, semantic_bytes),
            allow_exact_replay=True,
        )
    )

    assert result.valid
    assert result.exact_replay_fallback_count == 1


@pytest.mark.parametrize("retained_layout_count", (0, 1))
def test_generator_and_checker_use_declared_exact_fallback_for_incomplete_archive(
    retained_layout_count: int,
) -> None:
    runtime = two_problem_runtime(first_width=9.0, second_width=9.0)
    binding = runtime.replay_input.instances[1]
    verified = runtime.runtime_candidates[binding.problem_id]
    runtime.runtime_candidates[binding.problem_id] = replace(
        verified,
        rejection_layouts=verified.rejection_layouts[:retained_layout_count],
    )
    oracle_request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    unchecked = M8UncheckedBundleRequest(
        oracle_request=oracle_request,
        freeze_id=_FREEZE_ID,
        freeze_sha256=_FREEZE_SHA256,
    )

    generated = score_unchecked_fact_bundle(unchecked)

    assert len(generated.bundle.common_lemmas) == 1
    lemma = generated.bundle.common_lemmas[0]
    assert lemma.evidence_mode == "exact_replay"
    assert lemma.exact_replay_reason == "exact_survivor_unsupported_representation"
    assert len(lemma.inventory_classifications) == 1
    classification = lemma.inventory_classifications[0]
    assert classification.classification == "exact_survivor"
    assert classification.exact_replay_reason == "unsupported_representation"
    assert classification.frontier_ref is None
    assert classification.candidate_scalar_refs == ()
    assert classification.translation_batch_refs == ()
    assert generated.bundle.candidate_scalar_facts == ()
    assert generated.bundle.frontier_facts == ()
    assert generated.bundle.translation_batches == ()
    assert {item.classification for item in generated.bundle.influence_facts} == {
        "state_rejoin",
        "exact_transition",
    }
    exact_influences = tuple(
        item
        for item in generated.bundle.influence_facts
        if item.classification == "exact_transition"
    )
    assert exact_influences
    assert all(item.rejection_evidence == () for item in exact_influences)
    assert all(item.search_evidence == () for item in exact_influences)
    assert all(item.competitor_evidence == () for item in exact_influences)

    request = _check_request(unchecked, generated.semantic_bytes)
    denied = check_m8_common_fact_bundle(request)
    allowed = check_m8_common_fact_bundle(replace(request, allow_exact_replay=True))

    assert not denied.valid
    assert denied.failure_code == "implicit_exact_replay"
    assert denied.exact_replay_fallback_count == 0
    assert allowed.valid
    assert allowed.failure_code == "valid_common_facts"
    assert allowed.exact_replay_fallback_count == 1
