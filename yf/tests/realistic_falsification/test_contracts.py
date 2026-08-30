from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.realistic_falsification.contracts import (
    M11_ALLOWED_PROVENANCE,
    M11_CLAIM_CEILING,
    M11CorpusContract,
    M11EvidenceState,
    M11ExperimentContract,
    M11FieldProvenance,
    M11ParentBinding,
    M11SourceBinding,
    M11Thresholds,
    M11VerdictAction,
    M11VerdictResult,
    build_m11_contract,
    build_m11_parent_binding,
    build_m11_source_binding,
    build_m11_verdict,
)


def _parent(position: int = 1) -> M11ParentBinding:
    digit = format(position, "x")
    return build_m11_parent_binding(
        role=f"parent_{position}",
        repository_path=f"evidence/parent-{position}.json",
        schema_version=f"yieldforge.parent-{position}.v1",
        parent_semantic_id=f"yfparent-{digit * 24}",
        parent_content_sha256=f"sha256:{digit * 64}",
        raw_file_sha256=f"sha256:{digit * 64}",
    )


def _source(corpus: str, lineage: str, digit: str) -> M11SourceBinding:
    return build_m11_source_binding(
        corpus_id=corpus,
        source_lineage_id=lineage,
        source_uri=f"https://example.test/{lineage}",
        upstream_sha256=f"sha256:{digit * 64}",
        normalized_manifest_sha256=f"sha256:{digit * 64}",
        coordinate_units="unknown",
        geometry_provenance="source_observed",
    )


def _corpus(position: int) -> M11CorpusContract:
    corpus_id = f"corpus-{position}"
    source = _source(corpus_id, f"independent-lineage-{position}", str(position))
    return M11CorpusContract(
        source=source,
        calibration_stream_ids=tuple(f"{corpus_id}-calibration-{index:02d}" for index in range(8)),
        confirmation_stream_ids=tuple(
            f"{corpus_id}-confirmation-{index:02d}" for index in range(20)
        ),
        shuffled_twin_stream_ids=tuple(f"{corpus_id}-shuffled-{index:02d}" for index in range(20)),
        hard_null_fixture_ids=tuple(f"{corpus_id}-hard-null-{index:02d}" for index in range(3)),
        exact_audit_episode_ids=tuple(f"{corpus_id}-exact-audit-{index:02d}" for index in range(6)),
        events_per_stream=24,
    )


def _provenance() -> tuple[M11FieldProvenance, ...]:
    return (
        M11FieldProvenance(field_name="geometry", provenance="source_observed"),
        M11FieldProvenance(field_name="geometry_family", provenance="derived"),
        M11FieldProvenance(field_name="material_price", provenance="externally_anchored"),
        M11FieldProvenance(field_name="chronology", provenance="generated"),
        M11FieldProvenance(field_name="material_identity", provenance="assumed"),
    )


def _contract(**changes: object) -> M11ExperimentContract:
    kwargs: dict[str, object] = {
        "parents": (_parent(1), _parent(2)),
        "corpora": (_corpus(1), _corpus(2)),
        "field_provenance": _provenance(),
    }
    kwargs.update(changes)
    return build_m11_contract(**kwargs)  # type: ignore[arg-type]


def test_contract_freezes_census_thresholds_metrics_and_claim_ceiling() -> None:
    contract = _contract()

    assert len(contract.corpora) == 2
    for corpus in contract.corpora:
        assert len(corpus.calibration_stream_ids) == 8
        assert len(corpus.confirmation_stream_ids) == 20
        assert len(corpus.shuffled_twin_stream_ids) == 20
        assert corpus.events_per_stream == 24
        assert len(corpus.hard_null_fixture_ids) >= 3
        assert len(corpus.exact_audit_episode_ids) >= 6

    assert contract.thresholds == M11Thresholds()
    assert contract.thresholds.savings_red_below_percent == 1.5
    assert contract.thresholds.savings_green_minimum_percent == 2.5
    assert contract.thresholds.unknown_red_below_percentage_points == 0.5
    assert contract.thresholds.unknown_green_minimum_percentage_points == 1.5
    assert contract.thresholds.maximum_mean_immediate_sacrifice_percent == 0.5
    assert contract.thresholds.minimum_opportunity_frequency_percent == 20.0
    assert contract.thresholds.minimum_ordinary_availability_percent == 60.0
    assert contract.thresholds.minimum_remnant_realization_percent == 60.0
    assert contract.thresholds.maximum_top_10_concentration_percent == 25.0
    assert contract.thresholds.minimum_median_savings_percent_exclusive == 0.0
    assert contract.thresholds.minimum_lower_mean_bound_percent_exclusive == 0.0
    assert contract.thresholds.minimum_deployable_capture_percent == 50.0
    assert contract.thresholds.fixed_adverse_minimum_savings_percent == 1.5
    assert contract.thresholds.fixed_adverse_minimum_unknown_percentage_points == 0.5
    assert contract.metrics.full_future_savings == "100 * (B_i - F_i) / B_i"
    assert contract.metrics.unknown_future_contribution == "100 * (K_i - F_i) / B_i"
    assert contract.metrics.deployable_savings == "100 * (B_i - D_i) / B_i"
    assert contract.metrics.deployable_unknown_contribution == "100 * (D0_i - D_i) / B_i"
    assert contract.claim_ceiling == M11_CLAIM_CEILING
    assert "never_authorizes_productization" in contract.claim_ceiling
    assert contract.productization_authorized is False


def test_provenance_is_an_exhaustive_closed_enumeration() -> None:
    assert M11_ALLOWED_PROVENANCE == (
        "source_observed",
        "derived",
        "externally_anchored",
        "generated",
        "assumed",
    )
    assert {item.provenance for item in _provenance()} == set(M11_ALLOWED_PROVENANCE)

    with pytest.raises(ValidationError):
        M11FieldProvenance(field_name="geometry", provenance="realistic")  # type: ignore[arg-type]


def test_contract_and_nested_models_are_strict_and_frozen() -> None:
    contract = _contract()
    with pytest.raises(ValidationError, match="Instance is frozen"):
        contract.productization_authorized = True  # type: ignore[misc]

    payload = contract.corpora[0].model_dump(mode="python")
    payload["events_per_stream"] = 24.0
    with pytest.raises(ValidationError):
        M11CorpusContract.model_validate(payload, strict=True)

    source_payload = contract.corpora[0].source.model_dump(mode="python")
    source_payload["unexpected"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        M11SourceBinding.model_validate(source_payload, strict=True)

    threshold_payload = contract.thresholds.model_dump(mode="python")
    threshold_payload["savings_green_minimum_percent"] = 2.6
    with pytest.raises(ValidationError, match="thresholds differ"):
        M11Thresholds.model_validate(threshold_payload, strict=True)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("calibration_stream_ids", tuple(f"cal-{i}" for i in range(7))),
        ("confirmation_stream_ids", tuple(f"confirm-{i}" for i in range(19))),
        ("shuffled_twin_stream_ids", tuple(f"shuffle-{i}" for i in range(19))),
        ("events_per_stream", 23),
        ("hard_null_fixture_ids", ("null-1", "null-2")),
        ("exact_audit_episode_ids", tuple(f"audit-{i}" for i in range(5))),
    ],
)
def test_corpus_rejects_drift_from_frozen_population(field: str, replacement: object) -> None:
    payload = _corpus(1).model_dump(mode="python")
    payload[field] = replacement
    with pytest.raises(ValidationError):
        M11CorpusContract.model_validate(payload, strict=True)


def test_corpus_rejects_duplicate_or_overlapping_streams() -> None:
    payload = _corpus(1).model_dump(mode="python")
    payload["confirmation_stream_ids"] = (
        payload["calibration_stream_ids"][0],
        *payload["confirmation_stream_ids"][1:],
    )
    with pytest.raises(ValidationError, match="unique and disjoint"):
        M11CorpusContract.model_validate(payload, strict=True)


@pytest.mark.parametrize("mutation", ["one_corpus", "same_lineage", "same_corpus_id"])
def test_contract_rejects_nonindependent_two_corpus_census(mutation: str) -> None:
    corpora = [_corpus(1), _corpus(2)]
    if mutation == "one_corpus":
        corpora.pop()
    elif mutation == "same_lineage":
        duplicate_source = _source("corpus-2", "independent-lineage-1", "2")
        corpora[1] = corpora[1].model_copy(update={"source": duplicate_source})
    else:
        duplicate_source = _source("corpus-1", "independent-lineage-2", "2")
        corpora[1] = corpora[1].model_copy(update={"source": duplicate_source})

    with pytest.raises(ValidationError, match="two distinct"):
        build_m11_contract(
            parents=(_parent(1), _parent(2)),
            corpora=tuple(corpora),
            field_provenance=_provenance(),
        )


def test_identity_builders_are_content_addressed_and_recomputed() -> None:
    parent = _parent(1)
    source = _source("corpus-1", "lineage-1", "1")
    contract = _contract()

    for model, id_field, prefix in (
        (parent, "binding_id", "yfm11p-"),
        (source, "source_id", "yfm11s-"),
        (contract, "contract_id", "yfm11c-"),
    ):
        digest = semantic_sha256(model, excluded_fields={id_field, "content_sha256"})
        assert getattr(model, id_field) == f"{prefix}{digest[:24]}"
        assert model.content_sha256 == f"sha256:{digest}"


@pytest.mark.parametrize(
    ("factory", "id_field"),
    [
        (lambda: _parent(1), "binding_id"),
        (lambda: _source("corpus-1", "lineage-1", "1"), "source_id"),
        (_contract, "contract_id"),
    ],
)
def test_identity_models_reject_forgery(factory: Any, id_field: str) -> None:
    model = factory()
    payload = model.model_dump(mode="python")
    payload[id_field] = payload[id_field][:-1] + ("0" if payload[id_field][-1] != "0" else "1")

    with pytest.raises(ValidationError, match="identity does not match semantic content"):
        type(model).model_validate(payload, strict=True)


def test_contract_builder_revalidates_and_detaches_nested_inputs() -> None:
    corpus = _corpus(1)
    invalid_source = corpus.source.model_copy(update={"source_id": "forged"})
    invalid_corpus = corpus.model_copy(update={"source": invalid_source})

    with pytest.raises(ValidationError):
        build_m11_contract(
            parents=(_parent(1), _parent(2)),
            corpora=(invalid_corpus, _corpus(2)),
            field_provenance=_provenance(),
        )

    parent = _parent(1)
    valid = build_m11_contract(
        parents=(parent, _parent(2)),
        corpora=(_corpus(1), _corpus(2)),
        field_provenance=_provenance(),
    )
    object.__setattr__(parent, "role", "mutated")
    assert valid.parents[0] is not parent
    assert valid.parents[0].role == "parent_1"


@pytest.mark.parametrize(
    ("state", "repair_count", "action"),
    [
        (M11EvidenceState.INVALID_TEST, 0, M11VerdictAction.ONE_REPAIR_AND_RERUN),
        (M11EvidenceState.INVALID_TEST, 1, M11VerdictAction.ABANDON),
        (M11EvidenceState.FALSIFIED_BY_OPTIMISTIC_CEILING, 0, M11VerdictAction.ABANDON),
        (M11EvidenceState.INSUFFICIENT_HEADROOM, 0, M11VerdictAction.ABANDON),
        (M11EvidenceState.RETAIN_FOR_PILOT, 0, M11VerdictAction.CONTINUE_TO_REAL_PILOT),
    ],
)
def test_verdict_builder_maps_every_branch_mechanically(
    state: M11EvidenceState,
    repair_count: int,
    action: M11VerdictAction,
) -> None:
    contract = _contract()
    result = build_m11_verdict(
        contract=contract,
        evidence_state=state,
        repair_count=repair_count,
    )

    assert result.action is action
    assert result.productization_authorized is False
    assert result.claim_ceiling == M11_CLAIM_CEILING
    assert result.result_id == f"yfm11r-{result.content_sha256[7:31]}"


def test_only_retain_for_pilot_can_continue() -> None:
    contract = _contract()
    for state in M11EvidenceState:
        result = build_m11_verdict(
            contract=contract,
            evidence_state=state,
            repair_count=0,
        )
        assert (result.action is M11VerdictAction.CONTINUE_TO_REAL_PILOT) is (
            state is M11EvidenceState.RETAIN_FOR_PILOT
        )


def test_verdict_repair_count_is_strict_and_bounded() -> None:
    result = build_m11_verdict(
        contract=_contract(),
        evidence_state=M11EvidenceState.INVALID_TEST,
        repair_count=0,
    )
    payload = result.model_dump(mode="python")

    for invalid_count in (0.0, 2, True):
        payload["repair_count"] = invalid_count
        with pytest.raises(ValidationError):
            M11VerdictResult.model_validate(payload, strict=True)


def test_verdict_rejects_caller_selected_action_and_forged_identity() -> None:
    contract = _contract()
    builder: Any = build_m11_verdict
    with pytest.raises(TypeError):
        builder(
            contract=contract,
            evidence_state=M11EvidenceState.INSUFFICIENT_HEADROOM,
            repair_count=0,
            action=M11VerdictAction.CONTINUE_TO_REAL_PILOT,
        )

    result = build_m11_verdict(
        contract=contract,
        evidence_state=M11EvidenceState.INSUFFICIENT_HEADROOM,
        repair_count=0,
    )
    payload = result.model_dump(mode="python")
    payload["action"] = M11VerdictAction.CONTINUE_TO_REAL_PILOT
    digest = semantic_sha256(payload, excluded_fields={"result_id", "content_sha256"})
    payload["result_id"] = f"yfm11r-{digest[:24]}"
    payload["content_sha256"] = f"sha256:{digest}"
    with pytest.raises(ValidationError, match="action does not match"):
        M11VerdictResult.model_validate(payload, strict=True)


def test_verdict_builder_revalidates_bypass_mutated_contract() -> None:
    contract = _contract()
    invalid = contract.model_copy(update={"claim_ceiling": "authorizes_productization"})

    with pytest.raises(ValidationError):
        build_m11_verdict(
            contract=invalid,
            evidence_state=M11EvidenceState.INSUFFICIENT_HEADROOM,
            repair_count=0,
        )
