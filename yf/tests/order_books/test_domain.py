import math
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from yieldforge.order_books.domain import (
    EconomicFields,
    FieldFamily,
    FieldFamilyProvenance,
    GenerationRegime,
    GenerationRequest,
    ProvenanceKind,
    RegimeThresholds,
)

COMMITTED_SLICE_SHA256 = "d1e6d6d6aa300f9699cc8d9ffb63cee1747735f640f2b5501298d383ea1402e8"


def test_generation_contracts_are_strict_frozen_and_forbid_extra_fields() -> None:
    request = GenerationRequest(
        regime=GenerationRegime.NO_SIGNAL,
        seed=17,
        event_count=8,
        starts_at=datetime(2026, 1, 1, tzinfo=UTC),
        interval_minutes=60,
        source_slice_sha256=COMMITTED_SLICE_SHA256,
        thresholds=RegimeThresholds(
            min_unique_task_refs=2,
            max_task_concentration=0.75,
        ),
    )

    with pytest.raises(ValidationError, match="frozen"):
        request.seed = 18  # type: ignore[misc]
    with pytest.raises(ValidationError, match="valid integer"):
        GenerationRequest(
            regime=GenerationRegime.NO_SIGNAL,
            seed="17",  # type: ignore[arg-type]
            event_count=8,
            starts_at=datetime(2026, 1, 1, tzinfo=UTC),
            interval_minutes=60,
            source_slice_sha256=COMMITTED_SLICE_SHA256,
        )
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        FieldFamilyProvenance.model_validate(
            {
                "family": "geometry",
                "kind": "source_observed",
                "explanation": "References the normalized source rows without mutation.",
                "surprise": True,
            }
        )


def test_field_family_provenance_distinguishes_observed_generated_and_assumed() -> None:
    geometry = FieldFamilyProvenance(
        family=FieldFamily.GEOMETRY,
        kind=ProvenanceKind.SOURCE_OBSERVED,
        explanation="References normalized source rows without mutation.",
    )
    chronology = FieldFamilyProvenance(
        family=FieldFamily.CHRONOLOGY,
        kind=ProvenanceKind.GENERATED,
        explanation="Generated from starts_at and interval_minutes.",
    )
    material = FieldFamilyProvenance(
        family=FieldFamily.MATERIAL,
        kind=ProvenanceKind.ASSUMED,
        explanation="Synthetic material labels; the source has no material field.",
    )

    assert geometry.kind is ProvenanceKind.SOURCE_OBSERVED
    assert chronology.kind is ProvenanceKind.GENERATED
    assert material.kind is ProvenanceKind.ASSUMED


def test_generation_request_rejects_unpinned_source_content() -> None:
    with pytest.raises(ValidationError, match="Input should be"):
        GenerationRequest(
            regime=GenerationRegime.NO_SIGNAL,
            seed=17,
            event_count=2,
            starts_at=datetime(2026, 1, 1, tzinfo=UTC),
            interval_minutes=60,
            source_slice_sha256="a" * 64,
        )


def test_regime_thresholds_reject_empty_and_incoherent_declarations() -> None:
    with pytest.raises(ValidationError, match="at least one threshold"):
        RegimeThresholds()
    with pytest.raises(ValidationError, match="cannot exceed"):
        RegimeThresholds(min_task_concentration=0.8, max_task_concentration=0.4)
    with pytest.raises(ValidationError, match="cannot exceed"):
        RegimeThresholds(min_mean_task_parts=40.0, max_mean_task_parts=20.0)
    with pytest.raises(ValidationError, match="cannot exceed"):
        RegimeThresholds(min_total_part_references=100, max_total_part_references=10)


def test_float_contracts_reject_nonfinite_values() -> None:
    with pytest.raises(ValidationError, match="finite number"):
        EconomicFields(
            priority_score=0.5,
            value_index=math.inf,
            lead_time_minutes=60,
        )
    with pytest.raises(ValidationError, match="finite number"):
        RegimeThresholds(min_mean_task_parts=math.inf)
